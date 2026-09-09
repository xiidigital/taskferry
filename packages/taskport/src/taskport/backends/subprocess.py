"""A job backend that runs workloads as local child processes.

The zero-infrastructure `JobBackend`: what Cloud Run Jobs and Kubernetes Jobs do
remotely, this does with :mod:`subprocess`. Same `JobSpec`, same `Execution`,
same state machine — so the development loop and the production deployment
differ by one line of routing configuration.

```mermaid
flowchart LR
    SPEC["JobSpec<br/>command · env · timeout"]
    B["SubprocessJobBackend"]
    P["child process"]
    LOG["log file"]

    SPEC --> B --> P
    P -->|"stdout + stderr"| LOG
    B -->|"exit code"| SPEC
```

Security and honesty
--------------------

The command is executed **without a shell** (``shell=False``, argv list), so a
job name or argument can never turn into shell metacharacters.

``image`` is ignored — there is no container here — and the capability set says
so by omitting ``CPU``, ``MEMORY``, ``GPU`` and ``PARALLELISM``. A spec asking
for two GPUs is rejected at submit time instead of quietly running on the CPU and
producing results nobody can explain three days later.

Handles are process-local: the backend tracks children in memory, so an execution
id is only meaningful inside the process that created it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..capabilities import Capability, CapabilitySet
from ..core.provider import ProviderMetadata
from ..errors import ExecutionNotFound, SubmissionError, TaskportTimeoutError
from ..execution import (
    Execution,
    ExecutionId,
    ExecutionKind,
    ExecutionResult,
    ExecutionState,
    new_execution_id,
)
from ..ports import BaseBackend
from ..specs import ExecutionSpec, JobSpec

SUBPROCESS_CAPABILITIES = frozenset(
    {
        Capability.SUBMIT,
        Capability.STATE,
        Capability.RESULT,
        Capability.CANCEL,
        Capability.LOGS,
        Capability.TIMEOUT,
        Capability.ENVIRONMENT,
    }
)

TERMINATE_GRACE_SECONDS = 5.0
"""How long a terminated child gets to exit cleanly before it is killed."""


@dataclass
class _Record:
    """Live bookkeeping for one child process."""

    process: subprocess.Popen[bytes]
    spec: JobSpec
    log_path: Path
    execution: Execution
    cancelled: bool = False
    timed_out: bool = False


class SubprocessJobBackend(BaseBackend):
    """Runs :class:`~taskport.specs.JobSpec` workloads as child processes.

    Args:
        log_dir: Where combined stdout/stderr is written. Defaults to the system
            temporary directory.
        inherit_env: Whether the child inherits the parent's environment before
            ``spec.env`` is applied. Turn it off for a clean, reproducible
            environment closer to what a container gets.
    """

    def __init__(
        self,
        *,
        log_dir: str | os.PathLike[str] | None = None,
        inherit_env: bool = True,
    ) -> None:
        self._log_dir = Path(log_dir) if log_dir is not None else Path(tempfile.gettempdir())
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._inherit_env = inherit_env
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "subprocess"

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.JOB

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(SUBPROCESS_CAPABILITIES, provider=self.name)

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    # -- submission ------------------------------------------------------------ #
    def _submit(self, spec: ExecutionSpec) -> Execution:
        assert isinstance(spec, JobSpec)
        argv = list(spec.argv)
        if not argv:
            raise SubmissionError(
                f"job {spec.job!r} has no command; the subprocess backend needs an argv "
                "(image-only specs need a container runtime)",
                backend=self.name,
            )

        execution_id = new_execution_id(ExecutionKind.JOB)
        log_path = self._log_dir / f"{execution_id}.log"
        env = {**os.environ, **spec.env} if self._inherit_env else dict(spec.env)
        started = datetime.now(UTC)

        log_file = log_path.open("wb")
        try:
            process = subprocess.Popen(
                argv,
                env=env,
                cwd=spec.working_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        except OSError as exc:
            log_file.close()
            raise SubmissionError(
                f"could not start job {spec.job!r} ({argv[0]!r}): {exc}", backend=self.name
            ) from exc
        finally:
            # The child holds its own descriptor; ours would only keep the file
            # open past the child's exit.
            if not log_file.closed:
                log_file.close()

        execution = Execution(
            id=execution_id,
            kind=ExecutionKind.JOB,
            backend=self.name,
            state=ExecutionState.RUNNING,
            name=spec.name,
            created_at=started,
            started_at=started,
            external_id=str(process.pid),
            correlation=spec.correlation,
            provider_metadata=ProviderMetadata(
                provider="local",
                provider_id=str(process.pid),
                resource=str(log_path),
                labels=dict(spec.labels),
            ),
            metadata={"log_path": str(log_path), "profile": spec.profile},
        )
        with self._lock:
            self._records[str(execution_id)] = _Record(
                process=process, spec=spec, log_path=log_path, execution=execution
            )
        return execution

    # -- observation ------------------------------------------------------------ #
    def _record(self, execution_id: ExecutionId) -> _Record:
        with self._lock:
            record = self._records.get(str(execution_id))
        if record is None:
            raise ExecutionNotFound(
                f"job {execution_id!r} is unknown to this process", backend=self.name
            )
        return record

    def _get(self, execution_id: ExecutionId) -> Execution:
        record = self._record(execution_id)
        with self._lock:
            return self._refresh_locked(record)

    def _refresh_locked(self, record: _Record) -> Execution:
        """Recompute the execution from the child's real state. Caller holds the lock."""
        if record.execution.is_terminal:
            return record.execution

        returncode = record.process.poll()
        if returncode is None:
            if self._exceeded_timeout(record):
                self._stop(record, timed_out=True)
                returncode = record.process.returncode
            else:
                return record.execution

        record.execution = record.execution.evolve(
            state=self._state_for(record, returncode),
            finished_at=record.execution.finished_at or datetime.now(UTC),
            result=self._result_for(record, returncode),
        )
        return record.execution

    def _state_for(self, record: _Record, returncode: int | None) -> ExecutionState:
        if record.timed_out:
            return ExecutionState.TIMED_OUT
        if record.cancelled:
            return ExecutionState.CANCELLED
        return ExecutionState.SUCCEEDED if returncode == 0 else ExecutionState.FAILED

    def _result_for(self, record: _Record, returncode: int | None) -> ExecutionResult:
        error: str | None = None
        if record.timed_out:
            error = f"timed out after {record.spec.timeout.seconds}s"
        elif record.cancelled:
            error = "cancelled"
        elif returncode != 0:
            error = f"exited with code {returncode}"
        return ExecutionResult(
            value=returncode,
            error=error,
            error_type="JobFailed" if error else None,
            exit_code=returncode,
            logs_uri=str(record.log_path),
        )

    def _exceeded_timeout(self, record: _Record) -> bool:
        budget = record.spec.timeout.seconds
        if budget is None:
            return False
        started = record.execution.started_at
        if started is None:  # pragma: no cover - always set at submit
            return False
        return (datetime.now(UTC) - started).total_seconds() > budget

    def _wait(self, execution_id: ExecutionId, *, timeout: float | None) -> Execution:
        record = self._record(execution_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                execution = self._refresh_locked(record)
            if execution.is_terminal:
                return execution
            if deadline is not None and time.monotonic() >= deadline:
                raise TaskportTimeoutError(f"job {execution_id!r} did not finish within {timeout}s")
            time.sleep(0.02)

    def _result(self, execution_id: ExecutionId, *, timeout: float | None) -> ExecutionResult:
        execution = self._wait(execution_id, timeout=timeout)
        assert execution.result is not None  # every terminal state sets one
        return execution.result

    def _cancel(self, execution_id: ExecutionId) -> Execution:
        record = self._record(execution_id)
        with self._lock:
            if record.execution.is_terminal:
                return record.execution
            if record.process.poll() is None:
                self._stop(record, timed_out=False)
            record.cancelled = True
            return self._refresh_locked(record)

    def _stop(self, record: _Record, *, timed_out: bool) -> None:
        """Ask the child to exit, then insist. Caller holds the lock."""
        record.timed_out = timed_out
        record.process.terminate()
        try:
            record.process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            record.process.kill()
            record.process.wait()
        record.execution = record.execution.evolve(finished_at=datetime.now(UTC))

    def logs(self, execution_id: ExecutionId) -> str:
        """Read the captured output of a job. Requires ``Capability.LOGS``."""
        self.capabilities.require(Capability.LOGS)
        record = self._record(execution_id)
        try:
            return record.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stream_logs(
        self,
        execution_id: ExecutionId,
        *,
        poll_interval: float = 0.1,
        timeout: float | None = None,
    ) -> Iterator[str]:
        """Yield the job's output line by line *as it is produced*.

        Where :meth:`logs` returns the output once, this follows the growing log
        file — the way ``tail -f`` does — so a caller can watch a long-running job
        live::

            for line in backend.stream_logs(job_id):
                print(line)

        The iterator ends when the child reaches a terminal state and the last
        buffered output has been drained, or after ``timeout`` seconds if given.
        Lines are yielded without their trailing newline. Requires
        ``Capability.LOGS``.

        This is a genuine stream, not a poll-and-diff: a job backend that could
        only report a log *location* (Cloud Run, AWS Batch) does not advertise it,
        and callers reach for the provider's own log stream there.
        """
        self.capabilities.require(Capability.LOGS)
        record = self._record(execution_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            reader = record.log_path.open("r", encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - the file is created before the record
            return
        pending = ""
        with reader:
            while True:
                chunk = reader.read()
                if chunk:
                    pending += chunk
                    lines = pending.split("\n")
                    pending = lines.pop()  # keep the trailing partial line
                    yield from lines
                    continue
                with self._lock:
                    terminal = self._refresh_locked(record).is_terminal
                if terminal:
                    pending += reader.read()
                    remaining = pending.split("\n")
                    if remaining and remaining[-1] == "":
                        remaining.pop()  # no spurious empty line from a final newline
                    yield from remaining
                    return
                if deadline is not None and time.monotonic() > deadline:
                    if pending:
                        yield pending
                    return
                time.sleep(poll_interval)

    def close(self) -> None:
        """Terminate every child this backend still owns, then forget them."""
        with self._lock:
            records = list(self._records.values())
            self._records.clear()
        for record in records:
            if record.process.poll() is None:
                record.process.terminate()
                try:
                    record.process.wait(timeout=TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                    record.process.kill()


def make_subprocess_backend(**options: Any) -> SubprocessJobBackend:
    """Factory for configuration (``{"factory": "subprocess", "log_dir": ...}``)."""
    return SubprocessJobBackend(
        log_dir=options.get("log_dir"),
        inherit_env=bool(options.get("inherit_env", True)),
    )


__all__ = [
    "SUBPROCESS_CAPABILITIES",
    "TERMINATE_GRACE_SECONDS",
    "SubprocessJobBackend",
    "make_subprocess_backend",
]
