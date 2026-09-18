"""Django views for push-based task delivery.

```mermaid
flowchart LR
    CT["Cloud Tasks"]
    V["task_webhook"]
    H["execute_envelope"]
    FN["your function"]

    CT -->|"POST JSON"| V --> H --> FN
```

The agnostic receiver (:func:`taskferry.envelope.execute_envelope`) takes bytes, so
it works in any framework. This module is the four lines of Django glue on top —
URL wiring, status codes, CSRF exemption — for projects that would otherwise
write them by hand.

    # urls.py
    from taskferry_django.views import task_webhook

    urlpatterns = [path("_taskferry/execute", task_webhook)]

Status codes, and why they differ
---------------------------------

A push service decides whether to retry from the response, so the codes are
chosen to mean the right thing to it:

| Situation | Status | The queue should |
| --- | --- | --- |
| ran fine | 204 | acknowledge |
| body is not a valid envelope | 400 | give up — retrying cannot help |
| task name will not resolve | 404 | give up — permanent |
| the task itself raised | 500 | retry, per its own configuration |

A malformed body is *not* a 500. Retrying it forever would fill a dead-letter
queue with a bug that no redelivery can fix.

Security
--------

**This endpoint runs code and Taskferry does not authenticate it.** Verify the
OIDC token your push service sends, or keep the service private. And give
``REGISTRY`` an import allowlist — the task name arrives over the network. See
``docs/security.md``; :func:`make_task_webhook` exists to make both easy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from taskferry.envelope import execute_envelope
from taskferry.errors import FunctionResolutionError, SerializationError
from taskferry.functions import FunctionRegistry

logger = logging.getLogger("taskferry.django")


def make_task_webhook(
    *,
    registry: FunctionRegistry | None = None,
    authenticate: Callable[[HttpRequest], bool] | None = None,
) -> Callable[[HttpRequest], HttpResponse]:
    """Build a webhook view with your registry and your authentication.

    The recommended form, because both arguments matter::

        from taskferry import FunctionRegistry
        from taskferry_django.views import make_task_webhook

        task_webhook = make_task_webhook(
            registry=FunctionRegistry(allowed_modules=["myapp"]),
            authenticate=verify_oidc_token,
        )

    Args:
        registry: Where task names resolve. **Give it an allowlist.**
        authenticate: Called with the request; return ``False`` to reject with
            403. Taskferry cannot do this for you — it never sees your framework's
            request object, which is exactly what makes the receiver portable.
    """

    @csrf_exempt
    @require_POST
    def view(request: HttpRequest) -> HttpResponse:
        if authenticate is not None and not authenticate(request):
            return JsonResponse({"error": "unauthorized"}, status=403)
        return _execute(request, registry)

    return cast(Callable[[HttpRequest], HttpResponse], view)


@csrf_exempt
@require_POST
def task_webhook(request: HttpRequest) -> HttpResponse:
    """Execute a task delivered as a JSON push request.

    Wired without arguments this resolves task names through a permissive
    registry and performs **no authentication** — appropriate behind IAM or a
    private network, and not otherwise. Prefer :func:`make_task_webhook`.
    """
    return _execute(request, None)


def _execute(request: HttpRequest, registry: FunctionRegistry | None) -> HttpResponse:
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)

    try:
        execute_envelope(payload, registry=registry)
    except SerializationError as exc:
        # A malformed or version-skewed envelope. Retrying cannot fix it, so 400
        # tells the queue to stop rather than dead-lettering the same bug 25 times.
        logger.warning("taskferry: rejected a malformed envelope: %s", exc)
        return JsonResponse({"error": str(exc)}, status=400)
    except FunctionResolutionError as exc:
        # The name does not resolve, or the allowlist refused it. Permanent.
        logger.warning("taskferry: could not resolve a pushed task: %s", exc)
        return JsonResponse({"error": str(exc)}, status=404)

    # Anything the task itself raises propagates: Django turns it into a 500,
    # which is how the push service is told to retry per its own configuration.
    return HttpResponse(status=204)


__all__ = ["make_task_webhook", "task_webhook"]
