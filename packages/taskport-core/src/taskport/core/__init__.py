"""``taskport.core`` — the tiny shared substrate for the Taskport family.

This package deliberately stays small (ADR-0004). It contains only what is
*genuinely* transversal across Tasks, Jobs, Events and Schedules: identifiers,
correlation, provider metadata, the capability model, configuration primitives,
serialization, observability hooks, the lazy registry, delivery vocabulary and
the root error hierarchy.

Domain types (``JobSpec``, ``Event``, ``Schedule`` ...) live in their own
packages, not here — centralization is not a reason to promote a type.

End users normally do not depend on ``taskport-core`` directly; they install
``taskport-django`` / ``taskport-jobs`` / ``taskport-events`` /
``taskport-scheduler``, which depend on it.
"""

from __future__ import annotations

from .capabilities import Capability, CapabilitySet
from .config import (
    ProviderOptions,
    env_bool,
    env_int,
    env_str,
    require,
    resolve_factory,
)
from .correlation import (
    Correlation,
    current_correlation,
    ensure_correlation,
    use_correlation,
)
from .delivery import DeliveryGuarantee, Ordering
from .errors import (
    ConfigurationError,
    ProviderError,
    SerializationError,
    TaskportError,
    UnsupportedCapabilityError,
)
from .ids import TaskportId, is_taskport_id, new_id
from .observability import (
    ATTR_ATTEMPT,
    ATTR_CORRELATION_ID,
    ATTR_EVENT_ID,
    ATTR_JOB_ID,
    ATTR_PROVIDER,
    ATTR_PROVIDER_ID,
    ATTR_SCHEDULE_ID,
    ATTR_TASK_ID,
    SPAN_EVENT_CONSUME,
    SPAN_EVENT_PUBLISH,
    SPAN_JOB_POLL,
    SPAN_JOB_SUBMIT,
    SPAN_SCHEDULE_CREATE,
    SPAN_TASK_ENQUEUE,
    SPAN_TASK_EXECUTE,
    NoopTracer,
    Span,
    Tracer,
    get_tracer,
    set_tracer,
    span,
)
from .provider import ProviderMetadata
from .registry import LazyRegistry
from .serialization import JsonSerializer, Serializer, ensure_json_serializable
from .typing import JSONArray, JSONObject, JSONScalar, JSONValue

__version__ = "0.1.0"

__all__ = [
    "ATTR_ATTEMPT",
    "ATTR_CORRELATION_ID",
    "ATTR_EVENT_ID",
    "ATTR_JOB_ID",
    "ATTR_PROVIDER",
    "ATTR_PROVIDER_ID",
    "ATTR_SCHEDULE_ID",
    "ATTR_TASK_ID",
    "SPAN_EVENT_CONSUME",
    "SPAN_EVENT_PUBLISH",
    "SPAN_JOB_POLL",
    "SPAN_JOB_SUBMIT",
    "SPAN_SCHEDULE_CREATE",
    "SPAN_TASK_ENQUEUE",
    "SPAN_TASK_EXECUTE",
    "Capability",
    "CapabilitySet",
    "ConfigurationError",
    "Correlation",
    "DeliveryGuarantee",
    "JSONArray",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "JsonSerializer",
    "LazyRegistry",
    "NoopTracer",
    "Ordering",
    "ProviderError",
    "ProviderMetadata",
    "ProviderOptions",
    "SerializationError",
    "Serializer",
    "Span",
    "TaskportError",
    "TaskportId",
    "Tracer",
    "UnsupportedCapabilityError",
    "__version__",
    "current_correlation",
    "ensure_correlation",
    "ensure_json_serializable",
    "env_bool",
    "env_int",
    "env_str",
    "get_tracer",
    "is_taskport_id",
    "new_id",
    "require",
    "resolve_factory",
    "set_tracer",
    "span",
    "use_correlation",
]
