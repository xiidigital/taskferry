"""Application code for the composed flow: a task that publishes an event."""

from __future__ import annotations

from django.tasks import task

from taskport.core import ensure_correlation
from taskport.events import Event, InMemoryEventBus

# A process-wide bus the subscriber (in run.py) listens on.
BUS = InMemoryEventBus()


@task
def handle_upload(file_id: str) -> None:
    correlation = ensure_correlation()
    print(f"  [task]  handle_upload file={file_id} corr={correlation.correlation_id}")
    # Task → Event: announce the fact; consumers decide what to do (composition).
    BUS.publish(
        Event(
            type="file.uploaded",
            source="urn:svc:uploads",
            data={"file_id": file_id},
            correlation=correlation,
        )
    )
