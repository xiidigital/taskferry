"""LocalJobRunner — real subprocess execution for development and testing.

Runs the spec's ``command`` as a child process (never via a shell, so no shell
injection), captures combined output to a log file, and supports status polling,
cancellation and wall-clock timeout. Single-process: it tracks running jobs in
memory, so a handle is only meaningful within the process that created it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from taskport.core import CapabilitySet, ProviderMetadata, new_id

from ..capabilities import JobCapability
from ..errors import JobError, JobNotFoundError
from ..models import JobHandle, JobResult, JobSpec, JobStatus
from ..runner import BaseJobRunner

_LOCAL_CAPABILITIES = frozenset(
    {
        JobCapability.STATUS,
        JobCapability.CANCEL,
        JobCapability.LOGS,
        JobCapability.TIMEOUT,
        JobCapability.ENVIRONMENT_OVERRIDE,
    }
)


@dataclass
class _Record:
    proc: subprocess.Popen[bytes]
    spec: JobSpec
    log_path: Path
    started_at: datetime
    finished_at: datetime | None = None
    cancelled: bool = False
    timed_out: bool = False


class LocalJobRunner(BaseJobRunner):
    """Runs jobs as local child processes."""

    def __init__(self, *, log_dir: str | os.PathLike[str] | None = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else Path(tempfile.gettempdir())
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    @property
    def provider(self) -> str:
        return "local"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(_LOCAL_CAPABILITIES, provider="local")

    def _submit(self, spec: JobSpec) -> JobHandle:
        if not spec.command:
            raise JobError("LocalJobRunner requires a non-empty command")
        job_id = new_id("job")
        log_path = self._log_dir / f"{job_id}.log"
        env = {**os.environ, **spec.env}
        started_at = datetime.now(UTC)
        log_file = log_path.open("wb")
        try:
            proc = subprocess.Popen(
                list(spec.command),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
        except OSError as exc:
            log_file.close()
            raise JobError(f"failed to start local job {spec.name!r}: {exc}") from exc
        # The child inherits the fd; close our copy so the file is flushed/closed
        # once the child exits.
        log_file.close()
        record = _Record(proc=proc, spec=spec, log_path=log_path, started_at=started_at)
        with self._lock:
            self._records[job_id] = record
        metadata = ProviderMetadata(
            provider="local",
            provider_id=str(proc.pid),
            resource=str(log_path),
            labels=dict(spec.labels),
        )
        return JobHandle(
            id=job_id,
            name=spec.name,
            provider_metadata=metadata,
            created_at=started_at,
            correlation=spec.correlation,
        )

    def _record(self, handle: JobHandle) -> _Record:
        with self._lock:
            record = self._records.get(handle.id)
        if record is None:
            raise JobNotFoundError(f"unknown local job {handle.id!r}")
        return record

    def _poll(self, handle: JobHandle) -> JobResult:
        record = self._record(handle)
        logs_uri = str(record.log_path)

        if record.cancelled:
            return JobResult(
                handle=handle,
                status=JobStatus.CANCELLED,
                logs_uri=logs_uri,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )

        returncode = record.proc.poll()
        if returncode is None:
            # Still running — enforce the wall-clock timeout if one was set.
            if record.spec.timeout is not None:
                elapsed = (datetime.now(UTC) - record.started_at).total_seconds()
                if elapsed > record.spec.timeout:
                    self._terminate(record, timed_out=True)
                    return JobResult(
                        handle=handle,
                        status=JobStatus.FAILED,
                        error=f"timed out after {record.spec.timeout}s",
                        logs_uri=logs_uri,
                        started_at=record.started_at,
                        finished_at=record.finished_at,
                    )
            return JobResult(
                handle=handle,
                status=JobStatus.RUNNING,
                logs_uri=logs_uri,
                started_at=record.started_at,
            )

        if record.finished_at is None:
            record.finished_at = datetime.now(UTC)
        if record.timed_out:
            status = JobStatus.FAILED
            error: str | None = f"timed out after {record.spec.timeout}s"
        elif returncode == 0:
            status = JobStatus.SUCCEEDED
            error = None
        else:
            status = JobStatus.FAILED
            error = f"exited with code {returncode}"
        return JobResult(
            handle=handle,
            status=status,
            exit_code=returncode,
            error=error,
            logs_uri=logs_uri,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    def _cancel(self, handle: JobHandle) -> None:
        record = self._record(handle)
        if record.proc.poll() is None:
            self._terminate(record, timed_out=False)
        record.cancelled = True

    # -- helpers ------------------------------------------------------------ #
    def _terminate(self, record: _Record, *, timed_out: bool) -> None:
        record.proc.terminate()
        try:
            record.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            record.proc.kill()
            record.proc.wait()
        record.finished_at = datetime.now(UTC)
        record.timed_out = timed_out

    def wait(
        self, handle: JobHandle, *, poll_interval: float = 0.05, timeout: float | None = None
    ) -> JobResult:
        """Block until the job reaches a terminal state (test/CLI convenience)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self.get(handle)
            if result.is_terminal:
                return result
            if deadline is not None and time.monotonic() > deadline:
                return result
            time.sleep(poll_interval)


def make_local_job_runner(**kwargs: object) -> LocalJobRunner:
    """Factory for use with ``runners.register_spec`` / ``configure``."""
    log_dir = kwargs.get("log_dir")
    return LocalJobRunner(log_dir=log_dir)  # type: ignore[arg-type]


__all__ = ["LocalJobRunner", "make_local_job_runner"]
