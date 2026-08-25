from dataclasses import replace

import pytest

from brain.execution import (
    BinanceExecutionAdapter,
    BybitExecutionAdapter,
    CredentialError,
    ExecutionConfig,
    ExecutionCoordinator,
    ExecutionIntent,
    ExecutionMode,
    InMemoryExecutionAdapter,
    OrderRequest,
    OrderStatus,
    PositionSnapshot,
    ProtectionManager,
    deterministic_client_order_id,
)


def intent():
    return ExecutionIntent("BTCUSDT", "LONG", 100, 98, 104, 106, None, 1, 5, 2, approved=True)


def test_order_model_and_identity_are_deterministic():
    order = OrderRequest.from_intent(intent())
    assert order.client_order_id == deterministic_client_order_id(intent())
    assert order.side == "BUY"
    assert order.to_dict() == OrderRequest.from_intent(intent()).to_dict()


def test_coordinator_blocks_default_live_and_kill_switches():
    live = ExecutionCoordinator(InMemoryExecutionAdapter(), ExecutionConfig(mode=ExecutionMode.LIVE))
    assert live.submit_intent(intent()).reason == "LIVE_NOT_EXPLICITLY_ENABLED"
    killed = ExecutionCoordinator(InMemoryExecutionAdapter(), ExecutionConfig(global_kill_switch=True))
    assert killed.submit_intent(intent()).reason == "KILL_SWITCH"


def test_coordinator_prevents_duplicates_and_stale_intents():
    adapter = InMemoryExecutionAdapter()
    coordinator = ExecutionCoordinator(adapter)
    first = coordinator.submit_intent(intent(), now=10)
    duplicate = coordinator.submit_intent(intent(), now=10)
    stale = ExecutionCoordinator(adapter, ExecutionConfig(stale_intent_after=2)).submit_intent(intent(), as_of=1, now=10)
    assert first.status == "SUBMITTED"
    assert duplicate.status == "DUPLICATE"
    assert stale.reason == "STALE_INTENT"


def test_timeout_after_submit_reconciles_without_duplicate():
    class TimeoutAdapter(InMemoryExecutionAdapter):
        def submit_order(self, order):
            self.orders[order.client_order_id] = order
            raise TimeoutError("ack lost")

    outcome = ExecutionCoordinator(TimeoutAdapter()).submit_intent(intent())
    assert outcome.status == "RECONCILED"
    assert outcome.order.status == OrderStatus.NEW


def test_reconciliation_detects_position_discrepancies():
    adapter = InMemoryExecutionAdapter()
    adapter.positions.append(PositionSnapshot("BTCUSDT", "LONG", 0.5, 100, "PAPER"))
    result = adapter.reconcile(PositionSnapshot("BTCUSDT", "LONG", 1, 100, "PAPER"))
    assert result.status == "DISCREPANCY"
    assert result.discrepancies == ("QUANTITY_MISMATCH",)


def test_protection_requires_stop_and_target_and_verifies_them():
    manager = ProtectionManager()
    orders = manager.create_plan(intent())
    assert manager.verify(orders[0], orders).verified is True
    assert manager.verify(orders[0], ()).reason == "PROTECTION_MISSING"
    with pytest.raises(ValueError):
        manager.create_plan(replace(intent(), approved=False))


def test_paper_shadow_and_testnet_live_credentials_are_explicit():
    assert ExecutionConfig().mode is ExecutionMode.PAPER
    assert ExecutionConfig(mode=ExecutionMode.SHADOW).allows_submission("PAPER", "BTCUSDT")
    with pytest.raises(CredentialError):
        BybitExecutionAdapter(ExecutionConfig(mode=ExecutionMode.TESTNET))
    with pytest.raises(CredentialError):
        BinanceExecutionAdapter(ExecutionConfig(mode=ExecutionMode.LIVE, live_enabled=True))


def test_binance_and_bybit_normalize_canonical_orders():
    response = {"symbol": "btcusdt", "orderId": 7, "clientOrderId": "apex-x", "side": "BUY", "type": "MARKET", "origQty": "1", "executedQty": "0.5", "avgPrice": "100", "status": "PARTIALLY_FILLED"}
    for adapter in (BinanceExecutionAdapter(), BybitExecutionAdapter()):
        order = adapter.normalize_order(response)
        assert order.exchange == adapter.exchange
        assert order.status is OrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == 0.5


def test_invalid_health_and_unapproved_orders_never_submit():
    adapter = InMemoryExecutionAdapter()
    adapter.healthy = False
    assert ExecutionCoordinator(adapter).submit_intent(intent()).reason == "EXCHANGE_UNAVAILABLE"
    assert ExecutionCoordinator(InMemoryExecutionAdapter()).submit_intent(replace(intent(), approved=False)).reason == "RISK_NOT_APPROVED"
