"""Django Task extensions for portable Taskferry options."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.tasks.base import Task

from taskferry.retry import RetryPolicy

_RETRY_POLICY_ATTRIBUTE = "__taskferry_retry_policy__"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskferryTask(Task):
    """Django's immutable Task with an optional portable retry policy.

    Django 6.1 passes custom ``@task`` keyword arguments to the configured
    backend's ``task_class``. On Django 6.0, use :func:`with_retry_policy` to
    attach the same metadata to the function before Django wraps it.
    """

    retry_policy: RetryPolicy | None = None

    def __post_init__(self) -> None:
        declared_policy = getattr(self.func, _RETRY_POLICY_ATTRIBUTE, None)
        if declared_policy is not None:
            if self.retry_policy is not None and self.retry_policy != declared_policy:
                raise ValueError(
                    "retry_policy was set both in @task() and with_retry_policy() "
                    "with different values"
                )
            if self.retry_policy is None:
                object.__setattr__(self, "retry_policy", declared_policy)

        if self.retry_policy is not None and not isinstance(self.retry_policy, RetryPolicy):
            raise TypeError("retry_policy must be a taskferry.RetryPolicy instance")

        # Task is a frozen dataclass; call the base implementation directly.
        Task.__post_init__(self)


def with_retry_policy(policy: RetryPolicy) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach a portable policy to a function before Django decorates it.

    This is the compatibility form for Django 6.0, whose ``@task`` decorator
    does not yet accept backend-specific keyword arguments::

        @task
        @with_retry_policy(RetryPolicy(max_attempts=4))
        def refresh_index(index_id: int) -> None:
            ...

    Django 6.1+ can express the same setting directly as
    ``@task(retry_policy=RetryPolicy(...))``.
    """
    if not isinstance(policy, RetryPolicy):
        raise TypeError("policy must be a taskferry.RetryPolicy instance")

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        previous = getattr(func, _RETRY_POLICY_ATTRIBUTE, None)
        if previous is not None and previous != policy:
            raise ValueError("with_retry_policy() was applied more than once with different values")
        setattr(func, _RETRY_POLICY_ATTRIBUTE, policy)
        return func

    return decorate


__all__ = ["TaskferryTask", "with_retry_policy"]
