"""Ordinary Django tasks. Nothing here mentions Taskport."""

from __future__ import annotations

from django.tasks import task


@task
def resize_image(image_id: int, width: int = 800) -> str:
    return f"resized image {image_id} to {width}px"


@task(queue_name="metadata")
def extract_metadata(dataset_id: int) -> dict[str, int]:
    return {"dataset_id": dataset_id, "bands": 4}
