"""The built-in backends, run against the same contract suites adapters use.

If a shipped backend cannot pass the contract, the contract is either wrong or
the backend is — and finding out here is much cheaper than finding out in a
third-party adapter.
"""

from __future__ import annotations

import sys

import tasks_fixture
from taskport.backends.inline import InlineExecutionBackend
from taskport.backends.process import ProcessTaskBackend
from taskport.backends.subprocess import SubprocessJobBackend
from taskport.backends.thread import ThreadTaskBackend
from taskport.contract import InlineBackendContract, JobBackendContract, TaskBackendContract
from taskport.functions import FunctionRegistry
from taskport.ports import ExecutionBackend
from taskport.specs import JobSpec, TaskSpec


class TestInlineBackendContract(InlineBackendContract):
    def make_backend(self) -> ExecutionBackend:
        return InlineExecutionBackend()


class TestThreadBackendContract(TaskBackendContract):
    reaches_terminal_state = True

    def make_backend(self) -> ExecutionBackend:
        registry = FunctionRegistry()
        registry.register(tasks_fixture.add, name="tasks_fixture:add")
        return ThreadTaskBackend(max_workers=2, registry=registry)

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="tasks_fixture:add", args=(1, 2))


class TestProcessBackendContract(TaskBackendContract):
    reaches_terminal_state = True

    def make_backend(self) -> ExecutionBackend:
        return ProcessTaskBackend(max_workers=1)

    def success_spec(self) -> TaskSpec:
        return TaskSpec(task="tasks_fixture:add", args=(1, 2))


class TestSubprocessBackendContract(JobBackendContract):
    reaches_terminal_state = True

    def make_backend(self) -> ExecutionBackend:
        return SubprocessJobBackend()

    def success_spec(self) -> JobSpec:
        return JobSpec(job="ok", command=[sys.executable, "-c", "pass"])


class TestSubprocessLogStreaming:
    """`stream_logs` follows a job's output live, unlike the one-shot `logs`."""

    def test_streams_each_line_as_the_job_produces_it(self) -> None:
        backend = SubprocessJobBackend()
        code = (
            "import time\n"
            "for i in range(3):\n"
            "    print('line', i, flush=True)\n"
            "    time.sleep(0.03)\n"
        )
        try:
            execution = backend.submit(JobSpec(job="chatty", command=[sys.executable, "-c", code]))
            lines = list(backend.stream_logs(execution.id, timeout=10))
        finally:
            backend.close()
        assert lines == ["line 0", "line 1", "line 2"]

    def test_stream_ends_and_drains_a_job_that_already_finished(self) -> None:
        backend = SubprocessJobBackend()
        try:
            execution = backend.submit(
                JobSpec(job="quick", command=[sys.executable, "-c", "print('only line')"])
            )
            backend.wait(execution.id, timeout=10)
            assert list(backend.stream_logs(execution.id, timeout=10)) == ["only line"]
        finally:
            backend.close()

    def test_stream_logs_needs_the_logs_capability(self) -> None:
        from taskport.capabilities import Capability

        backend = SubprocessJobBackend()
        try:
            assert Capability.LOGS in backend.capabilities  # so streaming is allowed here
        finally:
            backend.close()
