"""The Django bridge: settings in, portable specs out, and no leakage either way.

The two claims this file exists to check:

1. a Django application enqueues through the standard ``django.tasks`` API and
   reaches whichever engine the deployment configured;
2. nothing about Django reaches ``taskferry`` — the same configuration works in a
   process that has never imported Django at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.tasks import task
from django.test import override_settings

import taskferry_django
from taskferry import Capability, ExecutionKind, ExecutionState, Taskferry, TaskferryConfig
from taskferry_django import TaskferryBackend, config_from_settings, get_runtime, reset_runtime
from taskferry_django.checks import ID_LOCAL_IN_PRODUCTION, ID_NOT_WIRED, check_taskferry


@task
def add(a: int, b: int) -> int:
    return a + b


@task(queue_name="immediate")
def ping() -> str:
    return "pong"


@pytest.fixture(autouse=True)
def _fresh_runtime() -> None:
    reset_runtime()
    yield
    reset_runtime()


class TestConfigFromSettings:
    def test_the_taskferry_setting_becomes_a_config(self) -> None:
        config = config_from_settings()
        assert set(config.backends) == {"inline", "thread", "subprocess"}
        assert config.defaults[ExecutionKind.TASK] == "thread"

    @override_settings(TASKFERRY=None)
    def test_no_setting_falls_back_to_local(self) -> None:
        """A fresh project runs, visibly locally, rather than crashing or no-oping."""
        config = config_from_settings()
        assert config.backends.keys() == TaskferryConfig.local().backends.keys()

    @override_settings(TASKFERRY="not a dict")
    def test_a_non_dict_setting_raises_djangos_own_exception(self) -> None:
        """So it surfaces through manage.py check, in a form Django users know."""
        with pytest.raises(ImproperlyConfigured, match="must be a dict"):
            config_from_settings()

    @override_settings(TASKFERRY={"backends": {}, "defaults": {"task": "missing"}})
    def test_an_invalid_setting_is_reported_as_improperly_configured(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="is invalid"):
            config_from_settings()

    def test_the_runtime_is_shared_across_the_process(self) -> None:
        """One runtime, so one connection pool — not one per request."""
        assert get_runtime() is get_runtime()

    def test_reset_runtime_builds_a_fresh_one(self) -> None:
        first = get_runtime()
        reset_runtime()
        assert get_runtime() is not first


class TestTaskferryBackend:
    def test_a_standard_django_enqueue_reaches_the_configured_engine(self) -> None:
        """The application code is ordinary Django. That is the whole point."""
        result = add.enqueue(20, 22)
        assert result.id.startswith("task_")
        assert result.backend == "default"

        runtime = get_runtime()
        handle = runtime.get(result.id)
        assert handle.wait(10).state is ExecutionState.SUCCEEDED
        assert handle.result().value == 42

    def test_the_queue_name_drives_routing(self) -> None:
        """`queue_name="immediate"` matches a route; no provider is named in code."""
        backend = TaskferryBackend("default", {})
        spec = backend.build_spec(ping.get_backend() and ping, [], {})
        assert spec.queue == "immediate"
        assert get_runtime().router.resolve(spec) == "thread"

    def test_every_spec_points_at_the_one_dispatcher(self) -> None:
        """A Django Task is not callable, so the bridge ships an entry point.

        See taskferry_django.execute for the reasoning. The consequence tested
        here is that only one module needs to be importable by a worker, which
        makes an import allowlist trivial to write.
        """
        from taskferry_django.execute import DISPATCH_REF

        spec = TaskferryBackend("default", {}).build_spec(add, [1, 2], {})
        assert spec.task == DISPATCH_REF

    def test_the_real_task_identity_is_preserved_for_routing_and_logs(self) -> None:
        """One dispatcher must not make every task look identical."""
        spec = TaskferryBackend("default", {}).build_spec(add, [1, 2], {})
        assert spec.name == add.name
        assert spec.labels["django_task"] == add.module_path

    def test_arguments_travel_as_data_inside_the_payload(self) -> None:
        spec = TaskferryBackend("default", {}).build_spec(add, [1, 2], {"extra": "x"})
        assert spec.args == (add.module_path, [1, 2], {"extra": "x"})

    def test_the_dispatcher_really_runs_the_django_task(self) -> None:
        """The round trip, without any engine in the way."""
        from taskferry_django.execute import run_task

        assert run_task(add.module_path, [20, 22], {}) == 42

    def test_the_dispatcher_refuses_a_path_that_is_not_a_django_task(self) -> None:
        """It runs on a worker, on a payload from a queue. Strictness is the point."""
        from taskferry.errors import FunctionResolutionError
        from taskferry_django.execute import run_task

        with pytest.raises(FunctionResolutionError, match=r"not a django\.tasks Task"):
            run_task("os.path.join", ["a", "b"], {})

    def test_the_dispatcher_rejects_a_malformed_path(self) -> None:
        from taskferry.errors import FunctionResolutionError
        from taskferry_django.execute import run_task

        with pytest.raises(FunctionResolutionError, match=r"not a Django task path"):
            run_task("nodots", [], {})

    @override_settings(
        TASKS={
            "pinned": {
                "BACKEND": "taskferry_django.TaskferryBackend",
                "OPTIONS": {"backend": "inline"},
            }
        }
    )
    def test_a_pinned_backend_bypasses_routing(self) -> None:
        backend = TaskferryBackend("pinned", {"OPTIONS": {"backend": "thread"}})
        assert backend._pinned == "thread"

    def test_capabilities_come_from_the_routed_engine_not_a_hardcoded_flag(self) -> None:
        """Change the engine in settings and the honest answer changes with it."""
        backend = TaskferryBackend("default", {})
        capabilities = backend.taskferry_capabilities()
        assert Capability.CANCEL in capabilities  # the thread pool can cancel
        assert Capability.PRIORITY not in capabilities  # ...but is FIFO

    def test_get_result_refuses_rather_than_fabricating(self) -> None:
        """Django's API implies every backend can look a result up. Most cannot."""
        from taskferry.errors import TaskferryError

        backend = TaskferryBackend("default", {})
        with pytest.raises(TaskferryError, match=r"runtime\.get"):
            backend.get_result("task_whatever")

    def test_the_state_mapping_covers_every_portable_state(self) -> None:
        """A new ExecutionState must not silently fall through to READY."""
        from taskferry_django.backend import _STATUS_MAP

        assert set(_STATUS_MAP) == set(ExecutionState)


class TestOnCommit:
    @pytest.mark.django_db(transaction=True)
    def test_submission_waits_for_the_commit(self) -> None:
        """The classic race: a worker reaching a row before it is committed."""
        from django.db import transaction

        from taskferry import TaskSpec
        from taskferry_django import submit_on_commit

        submitted: list[str] = []
        runtime = get_runtime()

        with transaction.atomic():
            submit_on_commit(
                TaskSpec(task="tasks_fixture:add", args=(1, 1)),
                runtime=runtime,
                on_submitted=lambda handle: submitted.append(str(handle.id)),
            )
            assert submitted == [], "nothing may be enqueued before the commit"

        assert len(submitted) == 1, "the commit must trigger the submission"

    @pytest.mark.django_db(transaction=True)
    def test_a_rollback_enqueues_nothing(self) -> None:
        from django.db import transaction

        from taskferry import TaskSpec
        from taskferry_django import submit_on_commit

        submitted: list[str] = []
        with pytest.raises(RuntimeError), transaction.atomic():
            submit_on_commit(
                TaskSpec(task="tasks_fixture:add", args=(1, 1)),
                on_submitted=lambda handle: submitted.append(str(handle.id)),
            )
            raise RuntimeError("business rule failed")

        assert submitted == [], "a rolled-back transaction must enqueue nothing"


class TestSystemChecks:
    def test_a_valid_configuration_produces_no_errors(self) -> None:
        errors = [message for message in check_taskferry() if message.is_serious()]
        assert not errors, [message.msg for message in errors]

    @override_settings(
        TASKFERRY={"backends": {"broken": {"factory": "cloudrun"}}, "defaults": {"job": "broken"}}
    )
    def test_a_backend_that_cannot_be_built_is_reported(self) -> None:
        """ "Installed" and "constructible with these options" are different questions."""
        messages = check_taskferry()
        assert any("cannot be built" in message.msg for message in messages)

    @override_settings(
        TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
    )
    def test_a_configured_but_unwired_taskferry_is_flagged(self) -> None:
        messages = check_taskferry()
        assert any(message.id == ID_NOT_WIRED for message in messages)

    @override_settings(DEBUG=False)
    def test_in_process_backends_in_production_are_flagged(self) -> None:
        """They work, and they lose pending work on restart. Say so before launch."""
        messages = check_taskferry()
        assert any(message.id == ID_LOCAL_IN_PRODUCTION for message in messages)

    @override_settings(TASKFERRY={"backends": {"t": {"factory": "thread"}}})
    def test_missing_defaults_are_flagged(self) -> None:
        from taskferry_django.checks import ID_NO_DEFAULT

        messages = check_taskferry()
        assert any(message.id == ID_NO_DEFAULT for message in messages)


class TestNoLeakage:
    """The architectural claim, checked from the Django side."""

    def test_taskferry_works_in_a_process_without_django(self) -> None:
        """Run in a subprocess that never imports Django, and prove work happens."""
        probe = (
            "import sys;"
            "import taskferry;"
            "assert 'django' not in sys.modules, 'taskferry imported django';"
            "rt = taskferry.Taskferry.local();"
            "assert rt.inline.run(lambda a, b: a + b, 20, 22) == 42;"
            "assert 'django' not in sys.modules, 'running work imported django';"
            "print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip() == "ok"

    def test_the_same_config_dict_serves_django_and_plain_python(self) -> None:
        """Lift the setting verbatim into a FastAPI service and it still works."""
        from django.conf import settings

        raw: dict[str, Any] = settings.TASKFERRY
        standalone = Taskferry.from_mapping(raw)
        try:
            assert standalone.backend_names() == get_runtime().backend_names()
        finally:
            standalone.close()

    def test_the_django_package_exposes_only_the_bridge(self) -> None:
        """No parallel task API — Django already has one."""
        assert set(taskferry_django.__all__) == {
            "TaskferryBackend",
            "__version__",
            "config_from_settings",
            "get_runtime",
            "reset_runtime",
            "make_task_webhook",
            "run_task",
            "submit_on_commit",
            "task_on_commit",
            "task_webhook",
        }


class TestAsyncEnqueue:
    """Django's ``aenqueue`` over the bridge, as documented in docs/concurrency.md."""

    async def test_aenqueue_reaches_the_engine(self) -> None:
        """Django implements aenqueue over enqueue with sync_to_async, so the
        bridge inherits it. Claimed in the concurrency doc; checked here."""
        result = await add.aenqueue(20, 22)
        assert result.id.startswith("task_")

        runtime = get_runtime()
        handle = runtime.get(result.id)
        assert handle.wait(10).state is ExecutionState.SUCCEEDED
        assert handle.result().value == 42


class TestWebhookView:
    """The Django glue over the framework-agnostic receiver."""

    def _post(self, view, payload, **extra):
        from django.test import RequestFactory

        return view(
            RequestFactory().post(
                "/_taskferry/execute",
                data=json.dumps(payload),
                content_type="application/json",
                **extra,
            )
        )

    def test_a_valid_envelope_runs_and_returns_204(self) -> None:
        from taskferry import FunctionRegistry
        from taskferry.envelope import ENVELOPE_VERSION
        from taskferry_django.views import make_task_webhook

        ran: list[int] = []
        registry = FunctionRegistry()
        registry.register(lambda n: ran.append(n), name="tests:record")

        view = make_task_webhook(registry=registry)
        response = self._post(
            view, {"taskferry": ENVELOPE_VERSION, "task": "tests:record", "args": [42]}
        )
        assert response.status_code == 204
        assert ran == [42]

    def test_a_malformed_body_is_400_not_500(self) -> None:
        """Retrying cannot fix a malformed body, so the queue must be told to stop."""
        from django.test import RequestFactory

        from taskferry_django.views import task_webhook

        request = RequestFactory().post(
            "/_taskferry/execute", data="{not json", content_type="application/json"
        )
        assert task_webhook(request).status_code == 400

    def test_a_version_mismatch_is_400(self) -> None:
        from taskferry_django.views import task_webhook

        assert self._post(task_webhook, {"taskferry": "99", "task": "m:f"}).status_code == 400

    def test_an_unresolvable_task_is_404(self) -> None:
        """Permanent, so the push service should give up rather than retry."""
        from taskferry import FunctionRegistry
        from taskferry.envelope import ENVELOPE_VERSION
        from taskferry_django.views import make_task_webhook

        view = make_task_webhook(registry=FunctionRegistry(allowed_modules=["myapp"]))
        response = self._post(view, {"taskferry": ENVELOPE_VERSION, "task": "os:system"})
        assert response.status_code == 404

    def test_a_failing_task_propagates_so_the_queue_retries(self) -> None:
        from taskferry import FunctionRegistry
        from taskferry.envelope import ENVELOPE_VERSION
        from taskferry_django.views import make_task_webhook

        registry = FunctionRegistry()

        def boom() -> None:
            raise RuntimeError("transient")

        registry.register(boom, name="tests:boom")
        view = make_task_webhook(registry=registry)
        with pytest.raises(RuntimeError, match="transient"):
            self._post(view, {"taskferry": ENVELOPE_VERSION, "task": "tests:boom"})

    def test_authentication_is_enforced_when_supplied(self) -> None:
        """Taskferry cannot authenticate for you, but it makes doing so one argument."""
        from taskferry.envelope import ENVELOPE_VERSION
        from taskferry_django.views import make_task_webhook

        view = make_task_webhook(authenticate=lambda request: False)
        response = self._post(view, {"taskferry": ENVELOPE_VERSION, "task": "m:f"})
        assert response.status_code == 403

    def test_get_is_rejected(self) -> None:
        from django.test import RequestFactory

        from taskferry_django.views import task_webhook

        response = task_webhook(RequestFactory().get("/_taskferry/execute"))
        assert response.status_code == 405
