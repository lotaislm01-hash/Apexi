from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapter import ExchangeExecutionAdapter
from .model import ExecutionConfig, ExecutionMode, OrderRequest, OrderStatus, PositionSnapshot, ReconciliationResult


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    order: OrderRequest | None = None
    reason: str | None = None

    def to_dict(self):
        return {"status": self.status, "order": self.order.to_dict() if self.order else None, "reason": self.reason}


class ExecutionCoordinator:
    """Single safety gate and idempotent entry point for exchange execution."""

    def __init__(self, adapter: ExchangeExecutionAdapter, config: ExecutionConfig | None = None) -> None:
        self.adapter = adapter
        self.config = config or ExecutionConfig()
        self._requests: dict[str, str] = {}

    def submit_intent(self, intent, *, as_of: float | None = None, now: float = 0.0) -> ExecutionOutcome:
        if not intent.approved:
            return ExecutionOutcome("REJECTED", reason="RISK_NOT_APPROVED")
        if self.config.mode is ExecutionMode.LIVE and not self.config.allows_submission(self.adapter.exchange, intent.symbol):
            return ExecutionOutcome("REJECTED", reason="LIVE_NOT_EXPLICITLY_ENABLED")
        if not self.config.allows_submission(self.adapter.exchange, intent.symbol):
            return ExecutionOutcome("REJECTED", reason="EXECUTION_MODE_DISABLED")
        if self.config.global_kill_switch or self.config.exchange_kill_switch or intent.symbol.upper() in self.config.symbol_kill_switches:
            return ExecutionOutcome("REJECTED", reason="KILL_SWITCH")
        if as_of is not None and now - as_of > self.config.stale_intent_after:
            return ExecutionOutcome("REJECTED", reason="STALE_INTENT")
        if intent.quantity <= 0 or intent.entry <= 0 or intent.stop_loss <= 0:
            return ExecutionOutcome("REJECTED", reason="INVALID_ORDER_LEVELS")
        if intent.leverage > self.config.max_leverage:
            return ExecutionOutcome("REJECTED", reason="LEVERAGE_LIMIT")
        order = OrderRequest.from_intent(intent, exchange=self.adapter.exchange, mode=self.config.mode, created_time=now)
        fingerprint = str(order.to_dict())
        if order.client_order_id in self._requests:
            existing = self.adapter.get_order(order.client_order_id)
            if existing is not None:
                return ExecutionOutcome("DUPLICATE", existing, "DUPLICATE_INTENT")
            return ExecutionOutcome("REJECTED", reason="AMBIGUOUS_RETRY_REQUIRES_RECONCILIATION")
        if not self.adapter.health_check():
            return ExecutionOutcome("REJECTED", reason="EXCHANGE_UNAVAILABLE")
        self._requests[order.client_order_id] = fingerprint
        try:
            submitted = self.adapter.submit_order(order)
        except (TimeoutError, ConnectionError):
            existing = self.adapter.get_order(order.client_order_id)
            if existing is not None:
                return ExecutionOutcome("RECONCILED", existing, "TIMEOUT_RECONCILED")
            return ExecutionOutcome("UNKNOWN", reason="SUBMISSION_STATUS_UNKNOWN")
        return ExecutionOutcome("SUBMITTED", submitted)

    def reconcile(self, expected: PositionSnapshot | None = None) -> ReconciliationResult:
        return self.adapter.reconcile(expected)
