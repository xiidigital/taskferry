"""The surfaces third-party adapters build on: plugins, tracking, ports, hooks.

These have no users inside this repository beyond the shipped adapters, so they
are the parts most likely to be subtly wrong in a way nothing notices until
someone outside the project tries to use them.
"""

from __future__ import annotations

from typing import Any

import pytest

from taskferry import (
    Capability,
    CapabilitySet,
    Execution,
    ExecutionKind,
    ExecutionState,
    available_backends,
    register_backend,
    unregister_backend,
)
from taskferry.backends.inline import InlineExecutionBackend
from taskferry.errors import ConfigurationError, ExecutionNotFound, UnsupportedCapability
from taskferry.execution import ExecutionId, new_execution_id
from taskferry.hooks import BaseHook, HookChain, LoggingHook
from taskferry.plugins import build_backend, registered_backends, resolve_factory
from taskferry.ports import BaseBackend, accepts, supports
from taskferry.specs import ExecutionSpec, InlineSpec, JobSpec, TaskSpec
from taskferry.tracking import ExternalIdIndex, has_prefix, is_digits


class TestPlugins:
    def test_a_manually_registered_factory_wins(self) -> None:
        """So a test can shadow an installed adapter with a fake."""
        register_backend("shadow-probe", lambda **_: InlineExecutionBackend(), replace=True)
        try:
            assert isinstance(build_backend("shadow-probe", {}), InlineExecutionBackend)
            assert "shadow-probe" in registered_backends()
            assert "shadow-probe" in available_backends()
        finally:
            unregister_backend("shadow-probe")
        assert "shadow-probe" not in registered_backends()

    def test_registering_twice_is_refused_without_replace(self) -> None:
        register_backend("dup-probe", lambda **_: InlineExecutionBackend(), replace=True)
        try:
            with pytest.raises(ConfigurationError, match="already registered"):
                register_backend("dup-probe", lambda **_: InlineExecutionBackend())
        finally:
            unregister_backend("dup-probe")

    def test_unregistering_something_absent_is_silent(self) -> None:
        unregister_backend("never-existed")

    def test_the_builtin_backends_are_always_discoverable(self) -> None:
        """These ship with taskferry, so they are available with nothing installed."""
        discovered = available_backends()
        for expected in ("inline", "thread", "process", "subprocess"):
            assert expected in discovered

    def test_installed_adapters_are_discovered_through_entry_points(self) -> None:
        """The mechanism that lets a deployment name an adapter it never imported.

        Skipped when the adapters are not installed — which is exactly the
        situation CI's isolation job creates on purpose, to prove `taskferry`
        stands alone. In the workspace they are all present and this runs.
        """
        discovered = available_backends()
        adapters = {
            "procrastinate",
            "cloudrun",
            "cloudtasks",
            "sqs",
            "servicebus",
            "dramatiq",
            "aws-batch",
            "kubernetes",
        }
        if not adapters & set(discovered):
            pytest.skip("no adapter distributions installed in this environment")
        missing = sorted(adapters - set(discovered))
        assert not missing, f"some adapters are installed but not discoverable: {missing}"

    def test_an_import_string_is_accepted(self) -> None:
        factory = resolve_factory("taskferry.backends.inline:InlineExecutionBackend")
        assert isinstance(factory(), InlineExecutionBackend)

    def test_a_dotted_import_string_is_accepted(self) -> None:
        factory = resolve_factory("taskferry.backends.inline.InlineExecutionBackend")
        assert isinstance(factory(), InlineExecutionBackend)

    def test_an_unknown_name_lists_what_is_available(self) -> None:
        """The fix should usually be visible in the error itself."""
        with pytest.raises(ConfigurationError) as caught:
            resolve_factory("nosuchbackend")
        assert "available:" in str(caught.value)
        assert "inline" in str(caught.value)

    def test_a_missing_module_says_the_adapter_is_probably_not_installed(self) -> None:
        with pytest.raises(ConfigurationError, match="probably not installed"):
            resolve_factory("taskferry_nonexistent.backend:make")

    def test_a_missing_attribute_is_reported(self) -> None:
        with pytest.raises(ConfigurationError, match="has no attribute"):
            resolve_factory("taskferry.backends.inline:NoSuchClass")

    def test_a_non_callable_target_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="not callable"):
            resolve_factory("taskferry.backends.inline:INLINE_CAPABILITIES")

    def test_an_invalid_spec_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="expected"):
            resolve_factory(":")

    def test_bad_options_read_as_a_configuration_problem(self) -> None:
        """ "You passed an option this backend does not accept" is config, not a crash."""
        with pytest.raises(ConfigurationError, match="rejected its options"):
            build_backend("inline", {"nonexistent_option": 1})


class TestExternalIdIndex:
    def test_it_remembers_a_pairing(self) -> None:
        index = ExternalIdIndex()
        index.remember("task_abc", "4711")
        assert index.resolve("task_abc") == "4711"
        assert "task_abc" in index
        assert len(index) == 1

    def test_an_unknown_id_resolves_to_none(self) -> None:
        assert ExternalIdIndex().resolve("task_nope") is None

    def test_a_none_external_id_is_ignored(self) -> None:
        """A provider that returned no id must not poison the index."""
        index = ExternalIdIndex()
        index.remember("task_abc", None)
        assert len(index) == 0

    def test_it_evicts_the_oldest_entries(self) -> None:
        """A month-long producer must not accumulate a month-long map."""
        index = ExternalIdIndex(capacity=3)
        for n in range(5):
            index.remember(f"task_{n}", str(n))
        assert len(index) == 3
        assert index.resolve("task_0") is None
        assert index.resolve("task_4") == "4"

    def test_a_resolved_entry_is_kept_fresh(self) -> None:
        """Recently used ids survive eviction; that is the point of LRU here."""
        index = ExternalIdIndex(capacity=2)
        index.remember("a", "1")
        index.remember("b", "2")
        index.resolve("a")
        index.remember("c", "3")
        assert index.resolve("a") == "1"
        assert index.resolve("b") is None

    def test_zero_capacity_disables_tracking(self) -> None:
        index = ExternalIdIndex(capacity=0)
        index.remember("a", "1")
        assert index.resolve("a") is None

    def test_a_negative_capacity_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            ExternalIdIndex(capacity=-1)

    def test_a_recognised_engine_id_needs_no_prior_submission(self) -> None:
        """An id copied from a console works from any process."""
        numeric = ExternalIdIndex(recognises=is_digits)
        assert numeric.resolve("4711") == "4711"
        assert numeric.resolve("task_abc") is None

        paths = ExternalIdIndex(recognises=has_prefix("projects/"))
        assert paths.resolve("projects/p/locations/eu/executions/x") is not None
        assert paths.resolve("job_abc") is None

    def test_forget_and_clear(self) -> None:
        index = ExternalIdIndex()
        index.remember("a", "1")
        index.forget("a")
        assert index.resolve("a") is None
        index.forget("a")  # silent
        index.remember("b", "2")
        index.clear()
        assert len(index) == 0

    def test_repr_is_informative(self) -> None:
        assert "capacity=10000" in repr(ExternalIdIndex())


class _MinimalBackend(BaseBackend):
    """The least a backend can implement: submit only, nothing advertised beyond it."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def kind(self) -> ExecutionKind:
        return ExecutionKind.TASK

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet({Capability.SUBMIT}, provider="minimal")

    def _submit(self, spec: ExecutionSpec) -> Execution:
        return Execution(
            id=new_execution_id(ExecutionKind.TASK),
            kind=ExecutionKind.TASK,
            backend=self.name,
            state=ExecutionState.QUEUED,
            name=spec.name,
        )


class TestPortDefaults:
    """What an adapter gets for free, and what it is refused."""

    def test_a_minimal_backend_can_submit(self) -> None:
        assert _MinimalBackend().submit(TaskSpec(task="m:f")).state is ExecutionState.QUEUED

    def test_unadvertised_operations_raise_rather_than_no_op(self) -> None:
        backend = _MinimalBackend()
        execution = backend.submit(TaskSpec(task="m:f"))
        for operation in (backend.get, backend.cancel, backend.result, backend.wait):
            with pytest.raises(UnsupportedCapability):
                operation(execution.id)

    def test_the_default_get_reports_not_found_when_state_is_advertised(self) -> None:
        """An adapter that claims STATE but forgets _get must fail loudly."""

        class ClaimsState(_MinimalBackend):
            @property
            def capabilities(self) -> CapabilitySet:
                return CapabilitySet({Capability.SUBMIT, Capability.STATE}, provider="claims-state")

        with pytest.raises(ExecutionNotFound):
            ClaimsState().get(ExecutionId("task_anything"))

    def test_a_spec_of_the_wrong_kind_is_refused_with_an_explanation(self) -> None:
        backend = _MinimalBackend()
        with pytest.raises(TypeError, match="task backend and needs a TaskSpec"):
            backend.submit(JobSpec(job="j", command=["true"]))

    def test_close_is_a_safe_default(self) -> None:
        _MinimalBackend().close()

    def test_repr_identifies_the_backend(self) -> None:
        assert "minimal" in repr(_MinimalBackend())

    def test_supports_and_accepts_read_the_capability_set(self) -> None:
        backend = _MinimalBackend()
        assert supports(backend, Capability.SUBMIT)
        assert not supports(backend, Capability.CANCEL)
        assert accepts(backend, TaskSpec(task="m:f"))
        # A spec needing a capability this backend lacks is not accepted...
        assert not accepts(backend, TaskSpec(task="m:f", priority=5))
        # ...and neither is one of the wrong kind.
        assert not accepts(backend, JobSpec(job="j"))


class TestHookChain:
    def test_an_empty_chain_is_falsy_and_dispatches_nothing(self) -> None:
        chain = HookChain()
        assert not chain
        assert len(chain) == 0
        chain.before_submit(TaskSpec(task="m:f"), "any")

    def test_with_hook_returns_a_new_chain(self) -> None:
        original = HookChain()
        extended = original.with_hook(BaseHook())
        assert len(original) == 0
        assert len(extended) == 1

    def test_every_event_reaches_every_hook(self) -> None:
        seen: list[str] = []

        class Recorder(BaseHook):
            def before_submit(self, spec: ExecutionSpec, backend: str) -> None:
                seen.append("before_submit")

            def after_submit(self, spec: ExecutionSpec, execution: Execution) -> None:
                seen.append("after_submit")

            def on_submit_error(
                self, spec: ExecutionSpec, backend: str, exc: BaseException
            ) -> None:
                seen.append("on_submit_error")

            def before_execute(self, execution: Execution) -> None:
                seen.append("before_execute")

            def after_execute(self, execution: Execution, result: Any) -> None:
                seen.append("after_execute")

            def on_success(self, execution: Execution, result: Any) -> None:
                seen.append("on_success")

            def on_failure(self, execution: Execution, exc: BaseException) -> None:
                seen.append("on_failure")

            def on_cancel(self, execution: Execution) -> None:
                seen.append("on_cancel")

            def on_retry(self, execution: Execution, attempt: int, exc: BaseException) -> None:
                seen.append("on_retry")

        chain = HookChain([Recorder()])
        spec = TaskSpec(task="m:f")
        execution = Execution(
            id=new_execution_id(),
            kind=ExecutionKind.TASK,
            backend="x",
            state=ExecutionState.QUEUED,
        )
        error = RuntimeError("boom")

        chain.before_submit(spec, "x")
        chain.after_submit(spec, execution)
        chain.on_submit_error(spec, "x", error)
        chain.before_execute(execution)
        chain.after_execute(execution, None)
        chain.on_success(execution, None)
        chain.on_failure(execution, error)
        chain.on_cancel(execution)
        chain.on_retry(execution, 2, error)

        assert seen == [
            "before_submit",
            "after_submit",
            "on_submit_error",
            "before_execute",
            "after_execute",
            "on_success",
            "on_failure",
            "on_cancel",
            "on_retry",
        ]

    def test_a_failing_hook_does_not_stop_the_ones_after_it(self) -> None:
        """One broken exporter must not silence the rest of your telemetry."""
        reached: list[str] = []

        class Broken(BaseHook):
            def on_cancel(self, execution: Execution) -> None:
                raise RuntimeError("exporter is down")

        class Working(BaseHook):
            def on_cancel(self, execution: Execution) -> None:
                reached.append("still ran")

        execution = Execution(
            id=new_execution_id(),
            kind=ExecutionKind.TASK,
            backend="x",
            state=ExecutionState.CANCELLED,
        )
        HookChain([Broken(), Working()]).on_cancel(execution)
        assert reached == ["still ran"]

    def test_repr_names_the_hooks(self) -> None:
        assert "LoggingHook" in repr(HookChain([LoggingHook()]))

    def test_the_logging_hook_survives_every_event(self, caplog: pytest.LogCaptureFixture) -> None:
        hook = LoggingHook()
        execution = Execution(
            id=new_execution_id(),
            kind=ExecutionKind.TASK,
            backend="x",
            state=ExecutionState.FAILED,
            name="probe",
        )
        error = RuntimeError("boom")
        hook.after_submit(TaskSpec(task="m:f"), execution)
        hook.on_submit_error(TaskSpec(task="m:f"), "x", error)
        hook.on_failure(execution, error)
        hook.on_retry(execution, 2, error)
        hook.on_cancel(execution)

    def test_base_hook_methods_are_all_no_ops(self) -> None:
        """So a hook implements only what it cares about."""
        hook = BaseHook()
        execution = Execution(
            id=new_execution_id(),
            kind=ExecutionKind.INLINE,
            backend="x",
            state=ExecutionState.SUCCEEDED,
        )
        spec = InlineSpec(func=lambda: None)
        assert hook.before_submit(spec, "x") is None
        assert hook.after_submit(spec, execution) is None
        assert hook.on_submit_error(spec, "x", RuntimeError()) is None
        assert hook.before_execute(execution) is None
        assert hook.after_execute(execution, None) is None
        assert hook.on_success(execution, None) is None
        assert hook.on_failure(execution, RuntimeError()) is None
        assert hook.on_cancel(execution) is None
        assert hook.on_retry(execution, 2, RuntimeError()) is None
