from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
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

    def __init__(self, db_path: str | None = None) -> None:
        self.events: list[ExecutionEvent] = []
        self._connection = sqlite3.connect(db_path) if db_path else None
        if self._connection is not None:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS execution_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
                "client_order_id TEXT, event_time REAL NOT NULL, details_json TEXT NOT NULL)"
            )
            self._connection.commit()

    def record(self, event_type: str, *, client_order_id: str | None = None, event_time: float = 0.0, **details) -> ExecutionEvent:
        event = ExecutionEvent(event_type, client_order_id, event_time, details)
        self.events.append(event)
        if self._connection is not None:
            self._connection.execute(
                "INSERT INTO execution_events (event_type, client_order_id, event_time, details_json) VALUES (?, ?, ?, ?)",
                (event_type, client_order_id, event_time, json.dumps(details, sort_keys=True, separators=(",", ":"))),
            )
            self._connection.commit()
        return event

    def snapshot(self) -> list[dict[str, Any]]:
        if self._connection is None:
            return [event.to_dict() for event in self.events]
        rows = self._connection.execute(
            "SELECT event_type, client_order_id, event_time, details_json FROM execution_events ORDER BY id"
        ).fetchall()
        return [
            ExecutionEvent(event_type, client_order_id, event_time, json.loads(details_json)).to_dict()
            for event_type, client_order_id, event_time, details_json in rows
        ]
