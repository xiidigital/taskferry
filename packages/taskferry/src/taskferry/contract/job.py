"""The contract every :class:`~taskferry.ports.JobBackend` must satisfy."""

from __future__ import annotations

from abc import abstractmethod

import pytest

from ..capabilities import Capability
from ..errors import UnsupportedCapability
from ..execution import ExecutionKind
from ..ports import ExecutionBackend
from ..specs import JobSpec, Resources
from .base import ExecutionBackendContract


class JobBackendContract(ExecutionBackendContract):
    """Subclass in an adapter's test module and implement the two hooks.

    ::

        class TestCloudRun(JobBackendContract):
            def make_backend(self):
                return CloudRunJobBackend(project="p", location="eu", jobs_client=FakeJobs())

            def success_spec(self):
                return JobSpec(job="build-cog", image="gdal:latest")
    """

    @abstractmethod
    def make_backend(self) -> ExecutionBackend: ...

    @abstractmethod
    def success_spec(self) -> JobSpec: ...

    def test_is_a_job_backend(self) -> None:
        assert self.make_backend().kind is ExecutionKind.JOB

    def test_gpu_capability_matches_gpu_requests(self) -> None:
        """The assertion that matters most: never run a GPU job on the CPU quietly."""
        backend = self.make_backend()
        spec = self.success_spec().evolve(resources=Resources(gpu=1, gpu_type="nvidia-tesla-t4"))
        if Capability.GPU in backend.capabilities:
            assert backend.submit(spec).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(spec)

    def test_cpu_and_memory_capabilities_match_resource_requests(self) -> None:
        backend = self.make_backend()
        caps = backend.capabilities
        spec = self.success_spec().evolve(resources=Resources(cpu="1000m", memory="512Mi"))
        if Capability.CPU in caps and Capability.MEMORY in caps:
            assert backend.submit(spec).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(spec)

    def test_parallelism_capability_matches_array_jobs(self) -> None:
        backend = self.make_backend()
        spec = self.success_spec().evolve(parallelism=4)
        if Capability.PARALLELISM in backend.capabilities:
            assert backend.submit(spec).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(spec)

    def test_timeout_capability_matches_timeout_requests(self) -> None:
        from ..retry import TimeoutPolicy

        backend = self.make_backend()
        spec = self.success_spec().evolve(timeout=TimeoutPolicy(seconds=60))
        if Capability.TIMEOUT in backend.capabilities:
            assert backend.submit(spec).id
        else:
            with pytest.raises(UnsupportedCapability):
                backend.submit(spec)

    def test_exit_code_is_reported_when_results_are_supported(self) -> None:
        if not self.reaches_terminal_state:
            pytest.skip("backend does not execute work in this test environment")
        backend = self.make_backend()
        if Capability.RESULT not in backend.capabilities:
            pytest.skip("backend does not report results")
        execution = backend.submit(self.success_spec())
        final = self.wait_for_terminal(backend, execution)
        assert final.result is not None
        assert final.result.exit_code == 0, "a successful job exits zero"


__all__ = ["JobBackendContract"]
