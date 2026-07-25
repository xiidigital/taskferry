"""Standard Django tasks — no Taskport-specific API in your code."""

from __future__ import annotations

from django.tasks import task


@task
def resize_image(image_id: int) -> str:
    print(f"  [task] resizing image {image_id}")
    return f"resized:{image_id}"
