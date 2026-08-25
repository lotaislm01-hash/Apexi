from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionEvent:
    event_type: str
    client_order_id: str | None
    event_time: float
    details: dict[str, Any]

    def to_dict(self):
        return {
            "event_type": self.event_type,
            "client_order_id": self.client_order_id,
            "event_time": self.event_time,
            "details": dict(sorted(self.details.items())),
        }


class ExecutionLedger:
    """Append-only deterministic execution lifecycle record."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def record(self, event_type: str, *, client_order_id: str | None = None, event_time: float = 0.0, **details) -> ExecutionEvent:
        event = ExecutionEvent(event_type, client_order_id, event_time, details)
        self.events.append(event)
        return event

    def snapshot(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]
