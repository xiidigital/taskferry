"""The ``runners`` registry (sections 35, 10).

``from taskport.jobs import runners`` then ``runners["default"]``. A local runner
is registered as ``"default"`` out of the box; applications reconfigure it (and
add ``"heavy"``, ``"gpu"`` ...) via :meth:`LazyRegistry.configure` or
:meth:`register`. Construction is lazy, so no provider SDK is imported until the
matching runner is first accessed (section 31).
"""

from __future__ import annotations

from taskport.core import LazyRegistry

from .runner import JobRunner
from .runners.local import make_local_job_runner

runners: LazyRegistry[JobRunner] = LazyRegistry("job runner")

# Sensible zero-config default: a local subprocess runner.
runners.register("default", make_local_job_runner)

__all__ = ["runners"]
