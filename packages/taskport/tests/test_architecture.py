"""The tests that keep the architecture from eroding.

Every other test checks that the code works. These check that the *shape* is
still right — because "taskport imports no framework and no provider SDK" is not
a property you can maintain by intention. It is one import statement away from
being false at any moment, and once it is false, every downstream promise this
project makes goes with it.

```mermaid
flowchart BT
    PROV["provider adapters<br/>procrastinate · cloudrun · cloudtasks · jobs"]
    FW["framework adapters<br/>django"]
    TP["taskport"]
    STD["standard library"]

    PROV --> TP
    FW --> TP
    TP --> STD
```

Three independent checks, because each catches something the others cannot:

1. **Source scan** — parses every module's AST. Catches a forbidden import even
   inside a function, where a runtime check would never see it.
2. **Runtime check** — imports ``taskport`` in a clean subprocess and inspects
   ``sys.modules``. Catches a transitive import the AST cannot see.
3. **Metadata check** — reads the declared dependencies. Catches the case where
   the code is clean but ``pip install taskport`` still drags Django in.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest

PACKAGES_DIR = Path(__file__).resolve().parents[2]
TASKPORT_SRC = PACKAGES_DIR / "taskport" / "src" / "taskport"

#: Nothing in this list may appear anywhere in the taskport distribution.
#: A framework or a provider SDK in the core would mean the portability layer
#: has a preferred framework and a preferred cloud, which is the one thing it
#: must never have.
FORBIDDEN = (
    "django",
    "fastapi",
    "flask",
    "starlette",
    "procrastinate",
    "celery",
    "dramatiq",
    "rq",
    "redis",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "sqlalchemy",
    "boto3",
    "botocore",
    "google",
    "azure",
    "kubernetes",
    "confluent_kafka",
    "pika",
)

#: The only third-party imports allowed inside taskport, each with the reason it
#: does not violate "no dependencies". Both are proven harmless by
#: ``test_importing_taskport_pulls_in_nothing_heavy``, which checks what actually
#: lands in ``sys.modules`` in a clean process. Adding an entry here is an
#: architectural decision and needs an ADR.
ALLOWED_THIRD_PARTY: dict[str, set[str]] = {
    # taskport.contract ships the reusable suites an independently released
    # adapter must be able to import and run. pytest is a test-time import in a
    # module nothing else imports.
    "contract/base.py": {"pytest"},
    "contract/task.py": {"pytest"},
    "contract/job.py": {"pytest"},
    "contract/inline.py": {"pytest"},
    # The OpenTelemetry bridge is a declared optional extra, imported inside the
    # function that configures it. Without the extra installed the tracer stays
    # a no-op and this import never runs.
    "core/otel.py": {"opentelemetry"},
}


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """Every top-level module name imported by a file, at any nesting depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class TestCoreImportsNothing:
    """`taskport` depends on the standard library and nothing else."""

    @pytest.mark.parametrize("path", _python_files(TASKPORT_SRC), ids=lambda p: p.name)
    def test_no_forbidden_import_in_source(self, path: Path) -> None:
        offending = _imported_roots(path) & set(FORBIDDEN)
        assert not offending, (
            f"{path.relative_to(PACKAGES_DIR)} imports {sorted(offending)}. "
            f"taskport must not depend on a framework or a provider SDK — that belongs "
            f"in an adapter distribution."
        )

    def test_no_undeclared_third_party_import(self) -> None:
        """Catch *any* new third-party dependency, not just a known-bad one.

        The FORBIDDEN list can only block what someone thought to list. This
        blocks everything outside the standard library that is not explicitly
        justified in :data:`ALLOWED_THIRD_PARTY`.
        """
        unexpected: dict[str, list[str]] = {}
        stdlib = sys.stdlib_module_names
        for path in _python_files(TASKPORT_SRC):
            relative = str(path.relative_to(TASKPORT_SRC))
            allowed = ALLOWED_THIRD_PARTY.get(relative, set())
            outside = {
                root
                for root in _imported_roots(path)
                if root not in stdlib and root != "taskport" and not root.startswith("_")
            }
            if outside - allowed:
                unexpected[relative] = sorted(outside - allowed)
        assert not unexpected, (
            f"undeclared third-party imports in taskport: {unexpected}. The distribution "
            f"declares no dependencies; adding one is an architectural change that needs "
            f"an ADR and an entry in ALLOWED_THIRD_PARTY."
        )

    def test_the_allowlist_is_not_stale(self) -> None:
        """Every allowlist entry must still name a real file and a real import.

        Without this, a justified exemption outlives the code it justified and
        quietly becomes a hole.
        """
        stale: list[str] = []
        for relative, allowed in ALLOWED_THIRD_PARTY.items():
            path = TASKPORT_SRC / relative
            if not path.exists():
                stale.append(f"{relative} no longer exists")
                continue
            unused = allowed - _imported_roots(path)
            if unused:
                stale.append(f"{relative} no longer imports {sorted(unused)}")
        assert not stale, "ALLOWED_THIRD_PARTY is stale: " + "; ".join(stale)

    def test_importing_taskport_pulls_in_nothing_heavy(self) -> None:
        """The runtime check: what actually lands in ``sys.modules``.

        Run in a subprocess so this test's own imports cannot mask a leak.
        """
        probe = (
            "import sys, taskport, taskport.backends, taskport.cli;"
            f"forbidden={FORBIDDEN!r};"
            "leaked=sorted(m for m in forbidden if m in sys.modules);"
            "print(','.join(leaked))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )
        leaked = result.stdout.strip()
        assert not leaked, (
            f"importing taskport transitively imported {leaked}. Every provider SDK must be "
            f"imported lazily, inside the method that needs a client."
        )

    def test_taskport_declares_no_dependencies(self) -> None:
        """Clean code is not enough: the metadata has to be clean too."""
        pyproject = tomllib.loads(
            (PACKAGES_DIR / "taskport" / "pyproject.toml").read_text(encoding="utf-8")
        )
        dependencies = pyproject["project"].get("dependencies", [])
        assert dependencies == [], (
            f"taskport declares {dependencies}. `pip install taskport` must install nothing "
            f"but taskport."
        )

    def test_public_api_needs_no_deep_imports(self) -> None:
        """Everything an application needs is importable from the top level."""
        import taskport

        expected = {
            "Taskport",
            "TaskportConfig",
            "TaskSpec",
            "JobSpec",
            "InlineSpec",
            "Execution",
            "ExecutionHandle",
            "ExecutionState",
            "Capability",
            "RetryPolicy",
            "Router",
            "Route",
        }
        missing = expected - set(taskport.__all__)
        assert not missing, f"missing from the public API: {sorted(missing)}"
        for name in taskport.__all__:
            assert hasattr(taskport, name), f"__all__ lists {name!r} but it is not importable"


class TestAdapterDependencyGraph:
    """Adapters depend on taskport. Nothing depends on an adapter."""

    ADAPTERS: ClassVar[tuple[str, ...]] = (
        "taskport-procrastinate",
        "taskport-cloudtasks",
        "taskport-sqs",
        "taskport-servicebus",
        "taskport-dramatiq",
        "taskport-cloudrun",
        "taskport-jobs",
        "taskport-events",
        "taskport-scheduler",
        "taskport-django",
    )

    @pytest.mark.parametrize("dist", ADAPTERS)
    def test_adapter_depends_on_taskport_only(self, dist: str) -> None:
        """Required dependencies are taskport plus, at most, one framework."""
        pyproject = tomllib.loads(
            (PACKAGES_DIR / dist / "pyproject.toml").read_text(encoding="utf-8")
        )
        required = pyproject["project"].get("dependencies", [])
        assert any(dep.startswith("taskport") for dep in required), (
            f"{dist} must depend on taskport"
        )
        # Everything provider-specific belongs in an extra, so installing the
        # adapter never forces an SDK the deployment does not use.
        provider_deps = [
            dep
            for dep in required
            if not dep.startswith("taskport") and not dep.startswith("django")
        ]
        assert not provider_deps, (
            f"{dist} requires {provider_deps} unconditionally; move provider SDKs into "
            f"[project.optional-dependencies] so they stay opt-in."
        )

    @pytest.mark.parametrize("dist", ADAPTERS)
    def test_adapter_does_not_import_another_adapter(self, dist: str) -> None:
        """Adapters are siblings, not a chain — one may be installed without the rest."""
        others = {d.replace("-", "_") for d in self.ADAPTERS} - {dist.replace("-", "_")}
        src = PACKAGES_DIR / dist / "src"
        for path in _python_files(src):
            coupled = _imported_roots(path) & others
            assert not coupled, (
                f"{path.relative_to(PACKAGES_DIR)} imports {sorted(coupled)}. Adapters must "
                f"not depend on each other: each is installable on its own."
            )

    @pytest.mark.parametrize("dist", ADAPTERS)
    def test_adapter_owns_a_top_level_module(self, dist: str) -> None:
        """No adapter contributes to the ``taskport`` package.

        ``taskport`` ships ``__init__.py``, so it is a regular package. Another
        distribution dropping files into it would break under editable installs
        and is forbidden by PEP 420 anyway. See ADR-0013.
        """
        src = PACKAGES_DIR / dist / "src"
        assert not (src / "taskport").exists(), (
            f"{dist} still contributes to the taskport namespace; it must own "
            f"src/{dist.replace('-', '_')} instead."
        )
        assert (src / dist.replace("-", "_")).is_dir()


class TestNoQueueReimplementation:
    """Taskport routes work to engines. It does not become one.

    A grep is a blunt instrument, but the failure it guards against is real and
    gradual: a poller here, a lock table there, and eighteen months later the
    portability layer is a worse Celery. Anything matched here should be
    delegated to an engine — or, if it genuinely belongs, added to the allowlist
    with an ADR explaining why.
    """

    FORBIDDEN_CONCEPTS: ClassVar[dict[str, str]] = {
        "worker_heartbeat": "worker liveness belongs to the engine",
        "advisory_lock": "distributed locking belongs to the engine",
        "queue_poll": "polling a broker belongs to the engine",
        "reserve_job": "job reservation belongs to the engine",
        "worker_registry": "worker bookkeeping belongs to the engine",
        "dead_letter_queue": "DLQ handling belongs to the engine",
    }

    def test_core_does_not_grow_engine_features(self) -> None:
        found: list[str] = []
        for path in _python_files(TASKPORT_SRC):
            text = path.read_text(encoding="utf-8").lower()
            for concept, why in self.FORBIDDEN_CONCEPTS.items():
                if concept in text:
                    found.append(f"{path.name}: {concept!r} — {why}")
        assert not found, "taskport is reimplementing engine features:\n" + "\n".join(found)
