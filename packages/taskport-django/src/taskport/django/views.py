"""HTTP receive side for push backends (e.g. Cloud Tasks → Cloud Run).

Wire it up::

    # urls.py
    from taskport.django.views import task_webhook
    urlpatterns = [path("_taskport/execute", task_webhook)]

SECURITY (section 24): this endpoint executes tasks. It MUST NOT be publicly
reachable without authentication. Rely on the platform: require the Cloud Run
"invoker" IAM role and Cloud Tasks OIDC tokens so only the queue can call it, or
place it behind your own auth. This view intentionally does not implement its own
auth scheme; it validates the payload shape only.
"""

from __future__ import annotations

import json
from typing import cast

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .execution import execute_task_message
from .message import TaskMessage


@csrf_exempt
@require_POST
def task_webhook(request: HttpRequest) -> HttpResponse:
    """Execute a task delivered as a JSON push request.

    Returns 204 on success, 400 on a malformed body, 404 for an unknown task
    (permanent — retrying will not help), and 500 on execution error (so the
    push service retries per its configuration).
    """
    try:
        message = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(message, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)
    try:
        execute_task_message(cast(TaskMessage, message))
    except LookupError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except NotImplementedError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return HttpResponse(status=204)


__all__ = ["task_webhook"]
