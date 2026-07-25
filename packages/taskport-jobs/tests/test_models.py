"""Tests for taskport.jobs domain models."""

from __future__ import annotations

import pytest

from taskport.jobs import JobCapability, JobResources, JobSpec, JobStatus


def test_job_status_terminality() -> None:
    assert JobStatus.SUCCEEDED.is_terminal
    assert JobStatus.FAILED.is_terminal
    assert JobStatus.CANCELLED.is_terminal
    assert not JobStatus.RUNNING.is_terminal
    assert not JobStatus.PENDING.is_terminal


def test_job_spec_is_immutable_and_normalizes_collections() -> None:
    spec = JobSpec(name="etl", command=["python", "etl.py"], env={"A": "1"})
    assert spec.command == ("python", "etl.py")  # coerced to tuple
    with pytest.raises(AttributeError):
        spec.name = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        spec.env["B"] = "2"  # type: ignore[index]


def test_job_spec_validates_parallelism_and_timeout() -> None:
    with pytest.raises(ValueError):
        JobSpec(name="x", parallelism=0)
    with pytest.raises(ValueError):
        JobSpec(name="x", timeout=0)


def test_required_capabilities_maps_spec_fields() -> None:
    spec = JobSpec(
        name="heavy",
        command=["run"],
        timeout=60,
        parallelism=4,
        env={"K": "v"},
        resources=JobResources(cpu="2", memory="4Gi", gpu=1),
    )
    required = spec.required_capabilities()
    assert required == {
        JobCapability.TIMEOUT,
        JobCapability.PARALLELISM,
        JobCapability.ENVIRONMENT_OVERRIDE,
        JobCapability.CPU_OVERRIDE,
        JobCapability.MEMORY_OVERRIDE,
        JobCapability.GPU,
    }


def test_minimal_spec_requires_nothing() -> None:
    assert JobSpec(name="x", command=["true"]).required_capabilities() == frozenset()
