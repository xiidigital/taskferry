"""Tests for taskport-core. Targets the >=90% coverage bar for core (section 38)."""

from __future__ import annotations

import pytest

from taskport.core import (
    Capability,
    CapabilitySet,
    ConfigurationError,
    Correlation,
    DeliveryGuarantee,
    JsonSerializer,
    LazyRegistry,
    NoopTracer,
    ProviderMetadata,
    ProviderOptions,
    SerializationError,
    TaskportError,
    UnsupportedCapabilityError,
    current_correlation,
    ensure_correlation,
    ensure_json_serializable,
    env_bool,
    env_int,
    env_str,
    get_tracer,
    is_taskport_id,
    new_id,
    require,
    resolve_factory,
    set_tracer,
    span,
    use_correlation,
)
from taskport.core.observability import ATTR_PROVIDER, SPAN_JOB_SUBMIT


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #
def test_new_id_has_prefix_and_is_unique() -> None:
    a, b = new_id("job"), new_id("job")
    assert a.startswith("job_")
    assert a != b
    assert is_taskport_id(a)


def test_new_id_rejects_bad_prefix() -> None:
    with pytest.raises(ValueError):
        new_id("not a prefix")


def test_is_taskport_id_false_for_plain_string() -> None:
    assert not is_taskport_id("plainstring")


# --------------------------------------------------------------------------- #
# provider metadata
# --------------------------------------------------------------------------- #
def test_provider_metadata_is_immutable_and_copies() -> None:
    meta = ProviderMetadata(provider="gcp", labels={"team": "gis"})
    with pytest.raises(TypeError):
        meta.labels["team"] = "x"  # type: ignore[index]
    with pytest.raises(AttributeError):
        meta.provider = "aws"  # type: ignore[misc]
    updated = meta.with_provider_id("projects/x/tasks/1").with_labels(env="prod")
    assert updated.provider_id == "projects/x/tasks/1"
    assert updated.labels == {"team": "gis", "env": "prod"}
    assert meta.provider_id is None  # original untouched (immutability, section 6)


# --------------------------------------------------------------------------- #
# correlation
# --------------------------------------------------------------------------- #
def test_correlation_start_and_causation_chain() -> None:
    root = Correlation.start()
    assert root.causation_id is None
    child = root.caused(new_id("task"))
    assert child.correlation_id == root.correlation_id  # same flow
    assert child.causation_id is not None


def test_correlation_headers_roundtrip() -> None:
    root = Correlation.start().caused(new_id("task"))
    root = Correlation(
        correlation_id=root.correlation_id,
        causation_id=root.causation_id,
        trace_context={"traceparent": "00-abc-def-01", "tracestate": "a=1"},
    )
    headers = root.to_headers()
    restored = Correlation.from_headers(headers)
    assert restored.correlation_id == root.correlation_id
    assert restored.causation_id == root.causation_id
    assert restored.trace_context["traceparent"] == "00-abc-def-01"


def test_correlation_from_empty_headers_starts_fresh() -> None:
    fresh = Correlation.from_headers({})
    assert fresh.causation_id is None


def test_use_correlation_binds_and_restores() -> None:
    assert current_correlation() is None
    corr = Correlation.start()
    with use_correlation(corr):
        assert current_correlation() is corr
        assert ensure_correlation() is corr
    assert current_correlation() is None
    assert ensure_correlation().correlation_id  # starts fresh when unbound


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #
class _Cap(Capability):
    A = "a"
    B = "b"
    C = "c"


def test_capability_set_contains_require_and_missing() -> None:
    caps = CapabilitySet({_Cap.A, _Cap.B}, provider="local")
    assert _Cap.A in caps
    assert caps.supports(_Cap.B)
    assert not caps.supports(_Cap.C)
    assert caps.supports_all({_Cap.A, _Cap.B})
    assert caps.missing({_Cap.A, _Cap.C}) == frozenset({_Cap.C})
    caps.require(_Cap.A)  # no raise
    with pytest.raises(UnsupportedCapabilityError) as exc:
        caps.require(_Cap.C)
    assert exc.value.capability == "c"
    assert exc.value.provider == "local"
    assert isinstance(exc.value, TaskportError)


def test_capability_set_equality_len_iter_repr() -> None:
    caps = CapabilitySet({_Cap.A, _Cap.B})
    assert len(caps) == 2
    assert set(caps) == {_Cap.A, _Cap.B}
    assert caps == {_Cap.A, _Cap.B}
    assert caps == CapabilitySet({_Cap.B, _Cap.A})
    assert caps != CapabilitySet({_Cap.A})
    assert caps != "nope"
    assert "a" in repr(caps)
    assert hash(caps) == hash(CapabilitySet({_Cap.A, _Cap.B}))


def test_capability_is_stringlike() -> None:
    assert _Cap.A == "a"
    assert f"{_Cap.A}" == "a"


# --------------------------------------------------------------------------- #
# serialization
# --------------------------------------------------------------------------- #
def test_json_serializer_roundtrip() -> None:
    ser = JsonSerializer()
    payload = ser.dumps({"b": 1, "a": [1, 2, {"x": True}]})
    assert ser.loads(payload) == {"a": [1, 2, {"x": True}], "b": 1}
    assert ser.content_type == "application/json"


def test_json_serializer_rejects_non_serializable() -> None:
    with pytest.raises(SerializationError):
        JsonSerializer().dumps({"bad": {1, 2, 3}})  # type: ignore[dict-item]
    with pytest.raises(SerializationError):
        JsonSerializer().dumps(float("nan"))


def test_json_serializer_rejects_bad_payload() -> None:
    with pytest.raises(SerializationError):
        JsonSerializer().loads("{not json")


def test_ensure_json_serializable() -> None:
    assert ensure_json_serializable({"ok": 1}) == {"ok": 1}
    with pytest.raises(SerializationError):
        ensure_json_serializable(object())


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_env_helpers_read_from_injected_mapping() -> None:
    environ = {"REDIS_URL": "redis://x", "COUNT": "5", "FLAG": "yes"}
    assert env_str("REDIS_URL", environ=environ) == "redis://x"
    assert env_str("MISSING", "default", environ=environ) == "default"
    assert env_int("COUNT", environ=environ) == 5
    assert env_bool("FLAG", environ=environ) is True
    assert env_bool("ABSENT", True, environ=environ) is True


def test_env_required_and_validation_errors() -> None:
    with pytest.raises(ConfigurationError):
        env_str("NOPE", required=True, environ={})
    with pytest.raises(ConfigurationError):
        env_int("X", environ={"X": "notint"})
    with pytest.raises(ConfigurationError):
        env_bool("X", environ={"X": "maybe"})


def test_require_helper() -> None:
    assert require("v", "thing") == "v"
    with pytest.raises(ConfigurationError):
        require(None, "thing")


def test_provider_options_scoping() -> None:
    opts = ProviderOptions({"gcp": {"http_target": {"url": "u"}}, "aws": {"fifo": True}})
    assert opts.for_provider("gcp") == {"http_target": {"url": "u"}}
    assert opts.for_provider("unknown") == {}
    assert opts.option("aws", "fifo") is True
    assert opts.option("aws", "missing", "fallback") == "fallback"
    with pytest.raises(ConfigurationError):
        opts.option("aws", "missing")


def test_resolve_factory_forms_and_errors() -> None:
    assert resolve_factory("taskport.core:JsonSerializer") is JsonSerializer
    assert resolve_factory("taskport.core.JsonSerializer") is JsonSerializer
    with pytest.raises(ConfigurationError):
        resolve_factory("nomodulehere:Thing")
    with pytest.raises(ConfigurationError):
        resolve_factory("taskport.core:__version__")  # not callable
    with pytest.raises(ConfigurationError):
        resolve_factory("bogus")


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_lazy_registry_builds_lazily_and_caches() -> None:
    reg: LazyRegistry[list[int]] = LazyRegistry("thing")
    calls = {"n": 0}

    def factory() -> list[int]:
        calls["n"] += 1
        return [1]

    reg.register("default", factory)
    assert "default" in reg
    assert calls["n"] == 0  # not built until accessed
    first = reg["default"]
    second = reg["default"]
    assert first is second
    assert calls["n"] == 1  # cached
    assert reg.aliases() == ("default",)


def test_lazy_registry_spec_and_configure() -> None:
    reg: LazyRegistry[object] = LazyRegistry("serializer")
    reg.register_spec("json", "taskport.core:JsonSerializer", sort_keys=False)
    assert isinstance(reg["json"], JsonSerializer)

    reg.configure({"other": {"factory": "taskport.core:JsonSerializer"}})
    assert isinstance(reg["other"], JsonSerializer)


def test_lazy_registry_errors() -> None:
    reg: LazyRegistry[object] = LazyRegistry("thing")
    with pytest.raises(ConfigurationError):
        _ = reg["missing"]
    reg.register("x", lambda: 1)
    with pytest.raises(ConfigurationError):
        reg.register("x", lambda: 2)  # duplicate without replace
    reg.register("x", lambda: 2, replace=True)
    with pytest.raises(ConfigurationError):
        reg.configure({"bad": {"no_factory": 1}})


def test_lazy_registry_reset_and_clear() -> None:
    reg: LazyRegistry[list[int]] = LazyRegistry("thing")
    reg.register("a", lambda: [0])
    inst = reg["a"]
    reg.reset_instances()
    assert reg["a"] is not inst  # rebuilt
    reg.clear()
    assert reg.aliases() == ()


# --------------------------------------------------------------------------- #
# observability
# --------------------------------------------------------------------------- #
def test_noop_tracer_span_is_safe() -> None:
    assert isinstance(get_tracer(), NoopTracer)
    with span(SPAN_JOB_SUBMIT, {ATTR_PROVIDER: "local"}) as s:
        s.set_attribute("x", 1)
        s.record_exception(ValueError("y"))  # no-op, no raise


def test_set_tracer_is_used_then_restored() -> None:
    class _RecordingSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attrs[key] = value

        def record_exception(self, exc: BaseException) -> None: ...

        def __enter__(self) -> _RecordingSpan:
            return self

        def __exit__(self, *exc: object) -> None: ...

    created: list[_RecordingSpan] = []

    class _Tracer:
        def start_span(self, name: str, attributes: object = None) -> _RecordingSpan:
            s = _RecordingSpan()
            created.append(s)
            return s

    original = get_tracer()
    try:
        set_tracer(_Tracer())
        with span("taskport.task.enqueue") as s:
            s.set_attribute("k", "v")
        assert created and created[0].attrs == {"k": "v"}
    finally:
        set_tracer(original)
    assert isinstance(get_tracer(), NoopTracer)


# --------------------------------------------------------------------------- #
# delivery
# --------------------------------------------------------------------------- #
def test_delivery_guarantee_never_promises_exactly_once() -> None:
    values = {g.value for g in DeliveryGuarantee}
    assert values == {"at_most_once", "at_least_once"}
    assert "exactly_once" not in values
