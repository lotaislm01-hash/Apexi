from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapter import ExchangeExecutionAdapter
from .model import ExecutionConfig, ExecutionMode, OrderRequest, OrderStatus, PositionSnapshot, ReconciliationResult
from .lifecycle import ExecutionLedger
from .transport import ExecutionTransportError


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    order: OrderRequest | None = None
    reason: str | None = None

    def to_dict(self):
        return {"status": self.status, "order": self.order.to_dict() if self.order else None, "reason": self.reason}


class ExecutionCoordinator:
    """Single safety gate and idempotent entry point for exchange execution."""

    def __init__(self, adapter: ExchangeExecutionAdapter, config: ExecutionConfig | None = None, ledger: ExecutionLedger | None = None) -> None:
        self.adapter = adapter
        self.config = config or ExecutionConfig()
        self._requests: dict[str, str] = {}
        self.ledger = ledger or ExecutionLedger(self.config.state_db_path)
        self.last_transport_error: ExecutionTransportError | None = None

    def submit_intent(self, intent, *, as_of: float | None = None, now: float = 0.0) -> ExecutionOutcome:
        if not intent.approved:
            self.ledger.record("RISK_REJECTED", event_time=now, reason="RISK_NOT_APPROVED")
            return ExecutionOutcome("REJECTED", reason="RISK_NOT_APPROVED")
        if self.config.mode is ExecutionMode.LIVE and not self.config.allows_submission(self.adapter.exchange, intent.symbol):
            return ExecutionOutcome("REJECTED", reason="LIVE_NOT_EXPLICITLY_ENABLED")
        if not self.config.allows_submission(self.adapter.exchange, intent.symbol):
            return ExecutionOutcome("REJECTED", reason="EXECUTION_MODE_DISABLED")
        if self.config.global_kill_switch or self.config.exchange_kill_switch or intent.symbol.upper() in self.config.symbol_kill_switches:
            self.ledger.record("KILL_SWITCH", event_time=now, symbol=intent.symbol)
            return ExecutionOutcome("REJECTED", reason="KILL_SWITCH")
        if as_of is not None and now - as_of > self.config.stale_intent_after:
            return ExecutionOutcome("REJECTED", reason="STALE_INTENT")
        if intent.quantity <= 0 or intent.entry <= 0 or intent.stop_loss <= 0:
            return ExecutionOutcome("REJECTED", reason="INVALID_ORDER_LEVELS")
        notional = float(intent.quantity) * float(intent.entry)
        if self.config.max_order_notional > 0 and notional > self.config.max_order_notional:
            return ExecutionOutcome("REJECTED", reason="ORDER_NOTIONAL_LIMIT")
        if self.config.max_position_notional > 0 and notional > self.config.max_position_notional:
            return ExecutionOutcome("REJECTED", reason="POSITION_NOTIONAL_LIMIT")
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
        try:
            account = self.adapter.get_account_state()
        except (TimeoutError, ConnectionError, ExecutionTransportError):
            self.ledger.record("EXECUTION_FAILURE", event_time=now, reason="EXCHANGE_UNAVAILABLE")
            return ExecutionOutcome("REJECTED", reason="EXCHANGE_UNAVAILABLE")
        if float(account.get("available_balance", float("inf"))) < notional / max(float(intent.leverage), 1.0):
            return ExecutionOutcome("REJECTED", reason="INSUFFICIENT_BALANCE")
        self._requests[order.client_order_id] = fingerprint
        self.ledger.record("EXECUTION_APPROVED", client_order_id=order.client_order_id, event_time=now, exchange=self.adapter.exchange)
        try:
            submitted = self.adapter.submit_order(order)
        except (TimeoutError, ConnectionError, ExecutionTransportError) as error:
            self.last_transport_error = error if isinstance(error, ExecutionTransportError) else None
            try:
                existing = self.adapter.get_order(order.client_order_id)
            except (TimeoutError, ConnectionError, ExecutionTransportError):
                existing = None
            if existing is not None:
                self.ledger.record("RECONCILIATION", client_order_id=order.client_order_id, event_time=now, status="RECONCILED")
                return ExecutionOutcome("RECONCILED", existing, "TIMEOUT_RECONCILED")
            self.ledger.record("EXECUTION_FAILURE", client_order_id=order.client_order_id, event_time=now, reason="SUBMISSION_STATUS_UNKNOWN")
            return ExecutionOutcome("UNKNOWN", reason="SUBMISSION_STATUS_UNKNOWN")
        self.ledger.record("ORDER_SUBMITTED", client_order_id=order.client_order_id, event_time=now, status=submitted.status.value)
        if submitted.status is OrderStatus.FILLED:
            self.ledger.record("FILLED", client_order_id=order.client_order_id, event_time=now, quantity=submitted.filled_quantity)
        return ExecutionOutcome("SUBMITTED", submitted)

    def reconcile(self, expected: PositionSnapshot | None = None) -> ReconciliationResult:
        return self.adapter.reconcile(expected)

    def submit_order_request(self, order: OrderRequest, *, now: float = 0.0) -> ExecutionOutcome:
        """Submit a derived protection/order request through the same safety gate."""
        if not self.config.allows_submission(self.adapter.exchange, order.symbol):
            return ExecutionOutcome("REJECTED", reason="EXECUTION_MODE_DISABLED")
        if self.config.global_kill_switch or self.config.exchange_kill_switch:
            return ExecutionOutcome("REJECTED", reason="KILL_SWITCH")
        if order.leverage > self.config.max_leverage or order.quantity <= 0:
            return ExecutionOutcome("REJECTED", reason="INVALID_ORDER")
        if order.client_order_id in self._requests:
            existing = self.adapter.get_order(order.client_order_id)
            return ExecutionOutcome("DUPLICATE", existing, "DUPLICATE_ORDER")
        if not self.adapter.health_check():
            return ExecutionOutcome("REJECTED", reason="EXCHANGE_UNAVAILABLE")
        self._requests[order.client_order_id] = str(order.to_dict())
        try:
            submitted = self.adapter.submit_order(order)
        except (TimeoutError, ConnectionError, ExecutionTransportError) as error:
            self.last_transport_error = error if isinstance(error, ExecutionTransportError) else None
            try:
                existing = self.adapter.get_order(order.client_order_id)
            except (TimeoutError, ConnectionError, ExecutionTransportError):
                existing = None
            return ExecutionOutcome("RECONCILED", existing, "TIMEOUT_RECONCILED") if existing else ExecutionOutcome("UNKNOWN", reason="SUBMISSION_STATUS_UNKNOWN")
        return ExecutionOutcome("SUBMITTED", submitted)
