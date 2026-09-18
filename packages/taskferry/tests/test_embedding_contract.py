"""The surface an embedding library depends on, pinned.

Taskferry's normal tests check that Taskferry works. These check the shape a
*library* builds an adapter against — the promises someone else's code will break
on if they change, without any of their tests running in this repository.

```mermaid
flowchart LR
    LIB["a library<br/>(DRATL, or anything)"]
    PORT["its own Executor port"]
    AD["its taskferry adapter<br/>structural Protocols"]
    TP["taskferry"]

    LIB --> PORT --> AD --> TP
```

The pattern this pins is the one a well-behaved consumer uses, and it is worth
stating because it is what keeps both projects independent:

* the library defines **its own** execution port, in its own vocabulary;
* a separate adapter distribution depends on the library, never the reverse;
* that adapter declares Taskferry's runtime as a **structural `Protocol`**, so it
  imports, type-checks and unit-tests with Taskferry absent;
* the application injects a configured runtime.

Nothing here imports a consumer. These are assertions about Taskferry's own
surface, written from the outside in — a duck-typed stand-in stands for any
embedder, so the file stays honest whether or not a particular one exists.

The specific risk being managed: a consumer's adapter binds to attribute names,
call signatures and exception types. Renaming `handle.external_id`, making
`runtime.get` keyword-only, or raising a different type on a capability mismatch
are all source-compatible *within* Taskferry and all break an embedder silently.
"""

from __future__ import annotations

import inspect
import pickle
import sys
from typing import Any, Protocol, runtime_checkable

import pytest

from taskferry import (
    Capability,
    Execution,
    ExecutionHandle,
    ExecutionState,
    Resources,
    Taskferry,
    TaskferryError,
)
from taskferry.errors import UnsupportedCapability


# --------------------------------------------------------------------------- #
# The shape an embedder declares. Deliberately written the way a consumer would,
# not by importing Taskferry's own classes.
# --------------------------------------------------------------------------- #
@runtime_checkable
class EmbedderHandle(Protocol):
    """What a consumer's adapter reads off a handle."""

    id: Any
    external_id: Any
    backend: str
    state: Any
    kind: Any

    def result(self, timeout: float | None = None) -> Any: ...

    def refresh(self) -> Any: ...

    def wait(self, timeout: float | None = None) -> Any: ...


@runtime_checkable
class EmbedderRuntime(Protocol):
    """What a consumer's adapter calls on the runtime."""

    inline: Any
    tasks: Any
    jobs: Any

    def get(self, execution_id: Any, *, backend: str | None = None) -> Any: ...

    def cancel(self, execution_id: Any, *, backend: str | None = None) -> Any: ...

    def wait(
        self, execution_id: Any, *, timeout: float | None = None, backend: str | None = None
    ) -> Any: ...


@pytest.fixture
def runtime() -> Taskferry:
    rt = Taskferry.local()
    yield rt
    rt.close()


class TestTheRuntimeSurface:
    def test_the_runtime_satisfies_a_structural_protocol(self, runtime: Taskferry) -> None:
        """An embedder declares a Protocol so it can be imported without Taskferry."""
        assert isinstance(runtime, EmbedderRuntime)

    def test_all_three_facades_exist(self, runtime: Taskferry) -> None:
        """Inline, Task and Job — an embedder maps its own work onto all three."""
        for facade in ("inline", "tasks", "jobs"):
            assert hasattr(runtime, facade)
            assert callable(getattr(runtime, facade).submit)

    @pytest.mark.parametrize(
        ("method", "required"),
        [
            ("get", ["execution_id", "backend"]),
            ("cancel", ["execution_id", "backend"]),
            ("wait", ["execution_id", "timeout", "backend"]),
        ],
    )
    def test_lookup_signatures_are_stable(
        self, runtime: Taskferry, method: str, required: list[str]
    ) -> None:
        """`backend=` is what lets a consumer resume a handle in another process."""
        parameters = inspect.signature(getattr(runtime, method)).parameters
        missing = [name for name in required if name not in parameters]
        assert not missing, f"runtime.{method} lost {missing}; an embedder calls it by name"

    def test_the_job_facade_accepts_the_portable_vocabulary(self, runtime: Taskferry) -> None:
        """A consumer maps its own resource model onto exactly these names."""
        parameters = inspect.signature(runtime.jobs.submit).parameters
        for name in ("image", "command", "args", "env", "resources", "profile", "timeout"):
            assert name in parameters, f"jobs.submit lost {name!r}"

    def test_resources_is_the_portable_resource_model(self) -> None:
        """An embedder translates its own requirements into this and nothing else."""
        resources = Resources(cpu="2000m", memory="4Gi", gpu=1, gpu_type="nvidia-tesla-t4")
        assert (resources.cpu, resources.memory, resources.gpu) == ("2000m", "4Gi", 1)


class TestTheHandleSurface:
    def test_the_handle_satisfies_a_structural_protocol(self, runtime: Taskferry) -> None:
        handle = runtime.inline.submit(lambda: 1)
        assert isinstance(handle, EmbedderHandle)

    def test_the_two_ids_stay_distinct(self, runtime: Taskferry) -> None:
        """A consumer persists `id`; an operator reads `external_id` off a console."""
        handle = runtime.jobs.submit("probe", command=[sys.executable, "-c", "pass"])
        handle.wait(30)
        assert isinstance(str(handle.id), str) and str(handle.id)
        assert handle.external_id != str(handle.id)

    def test_the_id_is_a_plain_string_a_consumer_can_store(self, runtime: Taskferry) -> None:
        """It goes in a database column, a JSON manifest and a log line."""
        handle = runtime.inline.submit(lambda: 1)
        assert isinstance(handle.id, str)
        assert pickle.loads(pickle.dumps(str(handle.id))) == str(handle.id)
        assert len(str(handle.id)) <= 64, "a consumer's id column is usually varchar(64)"

    def test_the_backend_name_is_readable_for_a_later_lookup(self, runtime: Taskferry) -> None:
        """`handle.backend` plus `handle.id` is the whole durable reference."""
        handle = runtime.tasks.submit("tasks_fixture:add", 1, 1)
        handle.wait(10)
        assert isinstance(handle.backend, str) and handle.backend
        resumed = runtime.get(handle.id, backend=handle.backend)
        assert str(resumed.id) == str(handle.id)

    def test_a_handle_is_reconstructible_from_the_durable_reference_alone(
        self, runtime: Taskferry
    ) -> None:
        """The pattern an embedder uses to survive a coordinator restart."""
        handle = runtime.tasks.submit("tasks_fixture:add", 20, 22)
        handle.wait(10)

        # Everything a consumer is allowed to persist.
        reference = {"id": str(handle.id), "backend": handle.backend}

        resumed = runtime.get(reference["id"], backend=reference["backend"])
        assert resumed.state is ExecutionState.SUCCEEDED
        assert resumed.result().value == 42


class TestTheStateVocabulary:
    """A consumer maps these onto its own, usually coarser, states."""

    def test_the_states_a_consumer_maps_from(self) -> None:
        assert {s.value for s in ExecutionState} == {
            "pending",
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "unknown",
        }, (
            "the state vocabulary changed. A consumer's mapping function enumerates "
            "these by value, so an addition silently falls through to its default."
        )

    def test_states_are_plain_strings(self) -> None:
        """So a consumer can map on `.value` without importing the enum."""
        for state in ExecutionState:
            assert isinstance(state.value, str)
            assert state == state.value

    def test_terminality_is_readable_without_enumerating(self) -> None:
        """A consumer asks "is it over?" rather than listing terminal states."""
        for state in ExecutionState:
            assert isinstance(state.is_terminal, bool)
        assert ExecutionState.SUCCEEDED.is_terminal
        assert not ExecutionState.RUNNING.is_terminal


class TestCapabilityNegotiation:
    """The part an embedder must use, and the part it must not be surprised by."""

    def test_capabilities_are_readable_before_acting(self, runtime: Taskferry) -> None:
        """Pre-flight is possible — which is why raising later is acceptable."""
        handle = runtime.inline.submit(lambda: 1)
        assert Capability.CANCEL not in handle.capabilities
        assert Capability.RESULT in handle.capabilities

    def test_capabilities_are_readable_from_the_runtime_too(self, runtime: Taskferry) -> None:
        """A consumer's deployment policy checks a backend before routing at it."""
        assert Capability.CANCEL in runtime.capabilities("subprocess")
        assert Capability.GPU not in runtime.capabilities("subprocess")

    def test_an_unsupported_operation_raises_a_taskferry_error(self, runtime: Taskferry) -> None:
        """Everything a consumer can catch is one type.

        An adapter is expected to translate this into its own hierarchy — the
        same discipline Taskferry applies to provider SDKs. This test pins the
        type it has to translate *from*, because an embedder's `except` clause
        names it.
        """
        handle = runtime.inline.submit(lambda: 1)
        with pytest.raises(UnsupportedCapability) as caught:
            handle.cancel()
        assert isinstance(caught.value, TaskferryError), (
            "every catchable Taskferry error must subclass TaskferryError, so a "
            "consumer can translate the whole family with one except clause"
        )

    def test_every_public_error_subclasses_taskferry_error(self) -> None:
        """One `except TaskferryError` has to be enough at an integration boundary."""
        import taskferry

        errors = [
            getattr(taskferry, name)
            for name in taskferry.__all__
            if isinstance(getattr(taskferry, name), type)
            and issubclass(getattr(taskferry, name), BaseException)
        ]
        assert errors, "the public API should export its exception types"
        for error in errors:
            assert issubclass(error, TaskferryError), (
                f"{error.__name__} is exported but is not a TaskferryError; a consumer "
                f"translating errors at the boundary would miss it"
            )


class TestTheDispatcherPattern:
    """How an embedder reaches the Task primitive with non-importable work.

    A library whose unit of work is an *object* — a configured processor, a bound
    method, a closure — cannot hand it to a task worker: only a name and JSON
    cross a process boundary. Taskferry refuses such a callable at submit time
    rather than failing on the worker.

    The way through is one importable dispatcher plus the real identity as data,
    which is exactly what `taskferry_django` does for Django's Task objects. This
    pins that the pattern works, so an embedder can rely on it.
    """

    def test_a_stateful_callable_is_refused_early(self, runtime: Taskferry) -> None:
        """The subtle case: a bound method *has* an importable name.

        ``mypkg:Processor.run`` resolves — to the plain function, without
        ``self.factor``. Accepting it would move the failure from this terminal
        to a worker, as a confusing TypeError about a missing argument.
        """
        from taskferry.errors import FunctionResolutionError

        configured = _Processor(factor=3)
        with pytest.raises(FunctionResolutionError, match="state would be lost"):
            runtime.tasks.submit(configured.run)

    def test_a_callable_instance_is_refused_early(self, runtime: Taskferry) -> None:
        """Its name resolves to the class, so a worker would build a new object."""
        from taskferry.errors import FunctionResolutionError

        with pytest.raises(FunctionResolutionError, match=r"callable .* instance"):
            runtime.tasks.submit(_CallableProcessor(factor=3))

    def test_a_classmethod_is_still_accepted(self, runtime: Taskferry) -> None:
        """It re-binds correctly on import, so there is nothing to lose."""
        handle = runtime.tasks.submit(_Processor.describe)
        assert handle.result(10).value == "a processor"

    def test_the_same_callable_is_fine_inline(self, runtime: Taskferry) -> None:
        """Which is why an embedder can always fall back to Inline."""
        configured = _Processor(factor=3)
        assert runtime.inline.run(configured.run, 14) == 42

    def test_a_dispatcher_carries_the_identity_as_data(self, runtime: Taskferry) -> None:
        """One importable entry point; what to run travels as JSON arguments."""
        handle = runtime.tasks.submit("test_embedding_contract:dispatch", "triple", 14)
        handle.wait(10)
        assert handle.result().value == 42

    def test_the_dispatcher_keeps_the_real_identity_visible(self, runtime: Taskferry) -> None:
        """A shared entry point must not make every task look the same."""
        from taskferry import TaskSpec

        spec = TaskSpec(
            task="test_embedding_contract:dispatch",
            args=("triple", 14),
            name="triple",
            labels={"processor": "triple"},
        )
        assert spec.name == "triple"
        assert spec.labels["processor"] == "triple"
        assert runtime.submit(spec).result(10).value == 42


class _Processor:
    """A configured object, the shape a library's unit of work usually takes."""

    def __init__(self, factor: int) -> None:
        self.factor = factor

    def run(self, value: int) -> int:
        return value * self.factor

    @classmethod
    def describe(cls) -> str:
        return "a processor"


class _CallableProcessor:
    """A configured object that is itself callable."""

    def __init__(self, factor: int) -> None:
        self.factor = factor

    def __call__(self, value: int) -> int:
        return value * self.factor


#: The embedder's own registry, resolved on the worker rather than shipped.
_REGISTRY = {"triple": _Processor(factor=3)}


def dispatch(name: str, value: int) -> int:
    """The one importable entry point a task worker needs. See the class above."""
    return _REGISTRY[name].run(value)


class TestIndependenceIsStructural:
    def test_taskferry_needs_no_knowledge_of_its_embedders(self) -> None:
        """The dependency arrow points one way, and nothing here breaks that."""
        import taskferry

        assert not hasattr(taskferry, "dratl")
        assert taskferry.__all__, "the public API is the whole contract surface"

    def test_a_runtime_is_injectable(self) -> None:
        """An embedder never constructs one: the application supplies it."""
        from taskferry.backends.inline import InlineExecutionBackend

        injected = InlineExecutionBackend()
        rt = Taskferry(config=Taskferry.local().config, backends={"inline": injected})
        try:
            assert rt.backend("inline") is injected
        finally:
            rt.close()

    def test_an_execution_snapshot_is_a_plain_value(self, runtime: Taskferry) -> None:
        """An embedder can hold, copy and compare one without a live backend."""
        handle = runtime.inline.submit(lambda: 1)
        snapshot = handle.execution
        assert isinstance(snapshot, Execution)
        assert snapshot.evolve(name="renamed").name == "renamed"
        assert snapshot.name != "renamed", "snapshots are immutable"

    def test_a_handle_is_not_meant_to_be_pickled(self, runtime: Taskferry) -> None:
        """It holds a live backend. The durable reference is `id` + `backend`.

        Asserted so the limitation is explicit rather than discovered by an
        embedder trying to put one on a queue.
        """
        handle = runtime.inline.submit(lambda: 1)
        assert isinstance(handle, ExecutionHandle)
        with pytest.raises((TypeError, AttributeError, pickle.PicklingError)):
            pickle.dumps(handle)
