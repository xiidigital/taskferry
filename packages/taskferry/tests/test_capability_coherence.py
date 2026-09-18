"""The capability vocabularies must stay coherent across every distribution.

`taskferry.Capability` is the **execution** vocabulary: what a backend can do with
an inline call, a task or a job. Events and schedules are adjacent concerns with
genuinely different questions to answer — `FANOUT` and `REPLAY` mean nothing to a
job, `GPU` means nothing to a cron trigger — so each has its own enum on the same
`CapabilitySet` machinery.

```mermaid
flowchart BT
    EXEC["taskferry.Capability<br/>submit · state · cancel · gpu · ..."]
    EV["EventCapability<br/>fanout · replay · retention"]
    SCH["ScheduleCapability<br/>cron · timezone · pause"]
    BASE["taskferry.core.Capability<br/>the shared StrEnum base"]
    SET["CapabilitySet<br/>one implementation for all three"]

    EXEC --> BASE
    EV --> BASE
    SCH --> BASE
    BASE --> SET
```

That split is deliberate (ADR-0005), and it only works while three properties
hold. None of them was checked before this file existed — they held by luck:

1. **One base**, so `CapabilitySet` and its `require`/`missing` machinery serve
   all three and a router or CLI can ask any provider the same question.
2. **No colliding values.** If `ORDERING` meant one thing to an event bus and
   another to a task queue, a string comparison across vocabularies would be
   quietly wrong — which is exactly the per-domain-enum problem the refactor set
   out to fix.
3. **Every provider attributes its set**, so an error message names the backend
   that raised it.
"""

from __future__ import annotations

import itertools

import pytest

from taskferry.capabilities import Capability
from taskferry.core.capabilities import Capability as BaseCapability
from taskferry.core.capabilities import CapabilitySet
from taskferry.core.errors import UnsupportedCapabilityError


def _vocabularies() -> dict[str, type[BaseCapability]]:
    """The capability enums shipped across the workspace, where installed."""
    found: dict[str, type[BaseCapability]] = {"execution": Capability}
    try:
        from taskferry_events import EventCapability

        found["event"] = EventCapability
    except ImportError:  # pragma: no cover - not installed in the isolated run
        pass
    try:
        from taskferry_scheduler import ScheduleCapability

        found["schedule"] = ScheduleCapability
    except ImportError:  # pragma: no cover - not installed in the isolated run
        pass
    return found


VOCABULARIES = _vocabularies()


class TestOneSharedFoundation:
    @pytest.mark.parametrize("name", sorted(VOCABULARIES))
    def test_every_vocabulary_shares_the_base(self, name: str) -> None:
        vocabulary = VOCABULARIES[name]
        assert issubclass(vocabulary, BaseCapability), (
            f"{vocabulary.__name__} must subclass taskferry.core.Capability so that "
            f"CapabilitySet works for it unchanged"
        )

    @pytest.mark.parametrize("name", sorted(VOCABULARIES))
    def test_every_vocabulary_is_a_string_enum(self, name: str) -> None:
        """Values have to survive a log line, a wire format and a CLI argument."""
        vocabulary = VOCABULARIES[name]
        assert issubclass(vocabulary, str)
        for member in vocabulary:
            assert isinstance(member.value, str) and member.value

    @pytest.mark.parametrize("name", sorted(VOCABULARIES))
    def test_every_vocabulary_works_with_capability_set(self, name: str) -> None:
        """The point of one base: one set implementation serves all of them."""
        vocabulary = VOCABULARIES[name]
        members = list(vocabulary)
        capabilities = CapabilitySet(members[:1], provider=name)

        assert members[0] in capabilities
        assert capabilities.supports(members[0])
        assert capabilities.provider == name
        capabilities.require(members[0])

        if len(members) > 1:
            assert capabilities.missing(members) == frozenset(members[1:])
            with pytest.raises(UnsupportedCapabilityError) as caught:
                capabilities.require(members[1])
            assert name in str(caught.value), "an error must name the provider that raised it"


class TestNoCollidingValues:
    """The property that makes cross-vocabulary comparison safe."""

    @pytest.mark.parametrize(
        ("left", "right"), sorted(itertools.combinations(sorted(VOCABULARIES), 2))
    )
    def test_two_vocabularies_share_no_value(self, left: str, right: str) -> None:
        overlap = {c.value for c in VOCABULARIES[left]} & {c.value for c in VOCABULARIES[right]}
        assert not overlap, (
            f"{left} and {right} both define {sorted(overlap)}. Because these are string "
            f"enums, a shared spelling compares equal across vocabularies — so it must "
            f"mean the same thing in both, or be renamed in one. This is the per-domain "
            f"enum problem the refactor set out to remove."
        )

    def test_values_are_lowercase_snake_case(self) -> None:
        """A consistent spelling is what makes a CLI or a log line readable."""
        wrong: list[str] = []
        for name, vocabulary in VOCABULARIES.items():
            wrong += [
                f"{name}.{member.name}={member.value!r}"
                for member in vocabulary
                if member.value != member.value.lower().replace(" ", "_")
            ]
        assert not wrong, f"non-canonical capability values: {wrong}"

    def test_names_and_values_agree(self) -> None:
        """``Capability.CANCEL`` must be ``"cancel"``, so the two are interchangeable."""
        mismatched: list[str] = []
        for name, vocabulary in VOCABULARIES.items():
            mismatched += [
                f"{name}.{member.name} != {member.value!r}"
                for member in vocabulary
                if member.name.lower() != member.value
            ]
        assert not mismatched, f"member names and values disagree: {mismatched}"


class TestTheExecutionVocabularyIsShared:
    """One enum across all three execution kinds — the P7 fix, asserted."""

    def test_one_enum_answers_for_every_kind(self) -> None:
        """A router or a CLI asks the same question of a task and of a job."""
        from taskferry import Taskferry

        runtime = Taskferry.local()
        try:
            for backend_name in runtime.backend_names():
                capabilities = runtime.capabilities(backend_name)
                assert Capability.SUBMIT in capabilities
                # The same member object, across kinds — not a per-domain twin.
                assert all(isinstance(c, Capability) for c in capabilities)
        finally:
            runtime.close()

    def test_cancel_means_cancel_everywhere(self) -> None:
        """0.1 had JobCapability.CANCEL and TaskCapability.CANCELLATION, unrelated."""
        from taskferry import Taskferry

        runtime = Taskferry.local()
        try:
            task_backend = runtime.backend("thread")
            job_backend = runtime.backend("subprocess")
            # One symbol, comparable across a task backend and a job backend.
            assert Capability.CANCEL in task_backend.capabilities
            assert Capability.CANCEL in job_backend.capabilities
        finally:
            runtime.close()

    def test_the_execution_vocabulary_covers_every_spec_requirement(self) -> None:
        """Nothing a spec can require may be absent from the enum."""
        from taskferry import JobSpec, Resources, TaskSpec, TimeoutPolicy
        from taskferry.retry import RetryPolicy

        demanding_task = TaskSpec(
            task="m:f",
            priority=5,
            idempotency_key="k",
            retry=RetryPolicy(max_attempts=3),
            timeout=TimeoutPolicy(seconds=60),
        )
        demanding_job = JobSpec(
            job="j",
            parallelism=4,
            env={"A": "1"},
            resources=Resources(cpu="1", memory="1Gi", gpu=1),
        )
        for spec in (demanding_task, demanding_job):
            for required in spec.required_capabilities():
                assert isinstance(required, Capability), (
                    f"{spec.kind.value} specs require {required!r}, which is not part of "
                    f"the execution vocabulary — a backend could never advertise it"
                )
