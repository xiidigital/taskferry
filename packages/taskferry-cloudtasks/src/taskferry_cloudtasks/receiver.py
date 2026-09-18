"""The receiving side — bytes in, task executed.

Cloud Tasks pushes an HTTP request; something in your service has to turn that
back into a function call. That something is :func:`handle_request`, and it takes
**bytes**, not a Django ``HttpRequest``, not a Starlette ``Request``.

```mermaid
flowchart LR
    CT["Cloud Tasks"]
    V["your view / route<br/>(4 lines, any framework)"]
    H["handle_request(body, headers)"]
    FN["package.module:function"]

    CT -->|"POST JSON"| V --> H --> FN
```

Django::

    from django.http import HttpResponse
    from taskferry_cloudtasks import handle_request

    def taskferry_execute(request):
        handle_request(request.body, dict(request.headers), registry=REGISTRY)
        return HttpResponse(status=204)

FastAPI::

    @app.post("/_taskferry/execute")
    async def execute(request: Request):
        handle_request(await request.body(), dict(request.headers), registry=REGISTRY)
        return Response(status_code=204)

Keeping the framework out of this module is what lets one adapter serve all of
them, and is why ``taskferry-cloudtasks`` depends on neither Django nor FastAPI.

Two things your endpoint must do
--------------------------------

**Authenticate.** Anyone who finds the URL can POST to it. Require the OIDC token
Cloud Tasks sends (verify it with ``google.oauth2.id_token``), or put the service
behind IAM. Taskferry cannot do this for you: it never sees your framework's
request object, which is the same reason it is portable.

**Be idempotent.** Cloud Tasks retries, so a task can arrive more than once.
Delivery is at-least-once; nothing here changes that and nothing can.

Returning 2xx acknowledges the task; raising (or returning 5xx) tells Cloud Tasks
to retry according to the queue's retry configuration. Let exceptions propagate
and the queue does the right thing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from taskferry.core.correlation import Correlation, use_correlation
from taskferry.envelope import read_envelope
from taskferry.errors import SerializationError
from taskferry.functions import FunctionRegistry, is_async_callable

from .backend import CloudTasksMessage


def parse_request(body: bytes | str) -> CloudTasksMessage:
    """Decode and validate the request body.

    Raises:
        SerializationError: when the body is not a Taskferry message of a version
            this code understands. Failing loudly is deliberate: a malformed body
            means a misconfigured endpoint or a version skew, and quietly doing
            nothing would hide both.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SerializationError(f"Cloud Tasks body is not valid JSON: {exc}") from exc
    return read_envelope(payload)


def handle_request(
    body: bytes | str,
    headers: Mapping[str, str] | None = None,
    *,
    registry: FunctionRegistry | None = None,
) -> Any:
    """Run the task described by a Cloud Tasks push request.

    Args:
        body: The raw request body.
        headers: Request headers, used to restore correlation and trace context
            so the task's spans join the flow that enqueued it.
        registry: Where task names resolve. **Give it an allowlist**: the task
            name arrives over the network, and an unrestricted resolver on a
            public endpoint is a remote-import primitive.

    Returns:
        Whatever the task returned. Cloud Tasks discards it; it is returned so
        the value is available to your view for logging or a response body.
    """
    message = parse_request(body)
    resolver = registry if registry is not None else FunctionRegistry()
    func = resolver.resolve(str(message["task"]))
    args = list(message.get("args") or [])
    kwargs = dict(message.get("kwargs") or {})

    with use_correlation(_correlation_from(message, headers)):
        if is_async_callable(func):
            import asyncio

            return asyncio.run(func(*args, **kwargs))
        return func(*args, **kwargs)


def _correlation_from(message: CloudTasksMessage, headers: Mapping[str, str] | None) -> Correlation:
    """Prefer the headers (they carry live trace context), fall back to the body."""
    if headers:
        normalised = {key.lower(): value for key, value in headers.items()}
        if "taskferry-correlation-id" in normalised:
            return Correlation.from_headers(normalised)
    embedded = message.get("correlation")
    return Correlation.from_headers(embedded) if embedded else Correlation.start()


__all__ = ["handle_request", "parse_request"]
