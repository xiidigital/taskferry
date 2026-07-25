"""The ``schedulers`` registry (sections 35, 12).

``from taskport.scheduler import schedulers`` then ``schedulers["default"]``. A
local scheduler is registered as ``"default"``; applications reconfigure it (and
add provider schedulers) via ``configure`` / ``register``. Lazy construction —
no provider SDK imported until first access.
"""

from __future__ import annotations

from taskport.core import LazyRegistry

from .adapters.local import make_local_scheduler
from .scheduler import Scheduler

schedulers: LazyRegistry[Scheduler] = LazyRegistry("scheduler")

# Zero-config default: an in-process scheduler.
schedulers.register("default", make_local_scheduler)

__all__ = ["schedulers"]
