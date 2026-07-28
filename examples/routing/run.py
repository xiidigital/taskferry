"""One call site, several engines — the whole point of the layer.

The application says *what kind of work this is*. Configuration says where that
runs. Nothing in ``business_logic`` below knows a provider exists.

    uv run python examples/routing/run.py
"""

from __future__ import annotations

from taskport import ExecutionKind, JobSpec, Taskport, TaskSpec

# --------------------------------------------------------------------------- #
# Application code. Written once, never edited again when the engine changes.
# --------------------------------------------------------------------------- #


def business_logic(runtime: Taskport) -> None:
    """Note what is absent: no provider name, no client, no SDK import."""
    runtime.tasks.submit("tasks:extract_metadata", 42, queue="metadata")
    runtime.tasks.submit("tasks:send_receipt", 42, queue="email")
    runtime.jobs.submit("build-cog", command=["python", "-c", "pass"], profile="heavy")


# --------------------------------------------------------------------------- #
# Deployment configuration. The only thing that differs per environment.
# --------------------------------------------------------------------------- #

DEVELOPMENT = {
    "backends": {
        "fast": {"factory": "thread", "max_workers": 4},
        "local-jobs": {"factory": "subprocess"},
    },
    "routes": [
        {"kind": "task", "queue": "metadata", "backend": "fast"},
        {"kind": "task", "queue": "email", "backend": "fast"},
        {"kind": "job", "profile": "heavy", "backend": "local-jobs"},
    ],
    "defaults": {"task": "fast", "job": "local-jobs"},
}

# The same application code pointed at real engines. Not executed here — it would
# need PostgreSQL and a GCP project — but this is the entire diff.
PRODUCTION = {
    "backends": {
        "pg": {"factory": "procrastinate", "app": "myapp.tasks:app"},
        "push": {
            "factory": "cloudtasks",
            "project": "my-project",
            "location": "europe-west1",
            "url": "https://svc.run.app/_taskport/execute",
        },
        "heavy": {"factory": "cloudrun", "project": "my-project", "location": "europe-west1"},
    },
    "routes": [
        {"kind": "task", "queue": "metadata", "backend": "pg"},
        {"kind": "task", "queue": "email", "backend": "push"},
        {"kind": "job", "profile": "heavy", "backend": "heavy"},
    ],
    "defaults": {"task": "pg", "job": "heavy"},
}

#: The three pieces of work ``business_logic`` submits, as routable probes.
WORK = [
    TaskSpec(task="tasks:extract_metadata", queue="metadata"),
    TaskSpec(task="tasks:send_receipt", queue="email"),
    JobSpec(job="build-cog", profile="heavy"),
]


def show(label: str, runtime: Taskport, *, explain: bool = False) -> None:
    print(f"\n{label}")
    for spec in WORK:
        key = "queue" if spec.kind is ExecutionKind.TASK else "profile"
        value = spec.queue if spec.kind is ExecutionKind.TASK else spec.profile
        print(f"  {spec.kind.value:<5} {key}={value:<9} -> {runtime.router.resolve(spec)}")
        if explain:
            print(f"        via {runtime.router.explain(spec)}")


def main() -> None:
    development = Taskport.from_mapping(DEVELOPMENT)
    production = Taskport.from_mapping(PRODUCTION)

    show("development:", development, explain=True)
    show("production (same application code):", production)

    print("\nNo backend was built for the production routing above. Routing is pure,")
    print("so inspecting it needed no PostgreSQL, no GCP project and no credentials.")

    print("\nRunning the development configuration for real:")
    business_logic(development)
    print("  three pieces of work submitted, two engines, zero infrastructure.")

    development.close()
    production.close()


if __name__ == "__main__":
    main()
