"""Tests for LocalJobRunner, including the reusable JobRunnerContract."""

from __future__ import annotations

import sys
from pathlib import Path

from taskport.jobs import JobResources, JobSpec, JobStatus, runners
from taskport.jobs.contract import JobRunnerContract
from taskport.jobs.runners.local import LocalJobRunner


def _py(*code: str) -> list[str]:
    return [sys.executable, "-c", *code]


class TestLocalRunnerContract(JobRunnerContract):
    supports_real_execution = True

    def make_runner(self) -> LocalJobRunner:
        return LocalJobRunner()

    def success_spec(self) -> JobSpec:
        return JobSpec(name="ok", command=_py("pass"))


def test_default_registry_runner_is_local() -> None:
    assert runners["default"].provider == "local"


def test_local_success_failure_and_logs(tmp_path) -> None:
    runner = LocalJobRunner(log_dir=tmp_path)

    ok = runner.run(JobSpec(name="ok", command=_py("print('hello')")))
    result = runner.wait(ok, timeout=10)
    assert result.status == JobStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.logs_uri
    assert "hello" in Path(result.logs_uri).read_text()

    bad = runner.run(JobSpec(name="bad", command=_py("import sys; sys.exit(3)")))
    result = runner.wait(bad, timeout=10)
    assert result.status == JobStatus.FAILED
    assert result.exit_code == 3


def test_local_cancel(tmp_path) -> None:
    runner = LocalJobRunner(log_dir=tmp_path)
    handle = runner.run(JobSpec(name="sleeper", command=_py("import time; time.sleep(30)")))
    runner.cancel(handle)
    result = runner.get(handle)
    assert result.status == JobStatus.CANCELLED


def test_local_timeout(tmp_path) -> None:
    runner = LocalJobRunner(log_dir=tmp_path)
    handle = runner.run(
        JobSpec(name="slow", command=_py("import time; time.sleep(30)"), timeout=0.2)
    )
    result = runner.wait(handle, timeout=10)
    assert result.status == JobStatus.FAILED
    assert result.error and "timed out" in result.error


def test_local_rejects_gpu_request(tmp_path) -> None:
    from taskport.core import UnsupportedCapabilityError

    runner = LocalJobRunner(log_dir=tmp_path)
    import pytest

    with pytest.raises(UnsupportedCapabilityError):
        runner.run(JobSpec(name="gpu", command=["true"], resources=JobResources(gpu=1)))
