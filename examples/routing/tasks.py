"""Task functions for the routing example.

Module level, so a worker in another process could import them by name — which
is exactly the constraint every real task engine imposes.
"""

from __future__ import annotations


def extract_metadata(dataset_id: int) -> dict[str, int]:
    return {"dataset_id": dataset_id, "bands": 4}


def send_receipt(order_id: int) -> str:
    return f"receipt sent for {order_id}"
