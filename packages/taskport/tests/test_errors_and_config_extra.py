"""Small extra coverage for the error hierarchy and config edge cases."""

from __future__ import annotations

import pytest

from taskport.core import (
    ConfigurationError,
    ProviderError,
    TaskportError,
    UnsupportedCapabilityError,
    env_int,
)


def test_provider_error_carries_provider_and_is_taskport_error() -> None:
    err = ProviderError("boom", provider="gcp")
    assert err.provider == "gcp"
    assert isinstance(err, TaskportError)
    assert str(err) == "boom"


def test_unsupported_capability_error_without_provider() -> None:
    err = UnsupportedCapabilityError("gpu")
    assert err.capability == "gpu"
    assert err.provider is None
    assert "gpu" in str(err)


def test_env_int_returns_default_when_absent() -> None:
    assert env_int("MISSING", 7, environ={}) == 7


def test_env_int_required_missing_raises() -> None:
    with pytest.raises(ConfigurationError):
        env_int("MISSING", required=True, environ={})
