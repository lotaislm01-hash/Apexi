from brain.execution import ExecutionConfig, ExecutionCoordinator, ExecutionLedger, ExecutionMode, PaperExecutionAdapter
from tests.execution.test_p3_foundation import intent


def test_paper_adapter_uses_canonical_order_lifecycle_and_position():
    ledger = ExecutionLedger()
    coordinator = ExecutionCoordinator(PaperExecutionAdapter(), ExecutionConfig(), ledger)
    outcome = coordinator.submit_intent(intent(), now=10)
    assert outcome.status == "SUBMITTED"
    assert outcome.order.status.value == "FILLED"
    assert outcome.order.filled_quantity == outcome.order.quantity
    assert ledger.snapshot()[-1]["event_type"] == "FILLED"
    assert coordinator.reconcile().actual.quantity == outcome.order.quantity


def test_ledger_is_deterministically_serializable():
    ledger = ExecutionLedger()
    ledger.record("INTENT_CREATED", event_time=1, symbol="BTCUSDT", reason="signal")
    assert ledger.snapshot() == [{
        "event_type": "INTENT_CREATED", "client_order_id": None, "event_time": 1,
        "details": {"reason": "signal", "symbol": "BTCUSDT"},
    }]


def test_sqlite_ledger_recovers_events_after_restart(tmp_path):
    path = str(tmp_path / "execution.sqlite3")
    first = ExecutionLedger(path)
    first.record("ORDER_SUBMITTED", client_order_id="apex-1", event_time=2, symbol="BTCUSDT")
    second = ExecutionLedger(path)
    assert second.snapshot() == [{
        "event_type": "ORDER_SUBMITTED", "client_order_id": "apex-1", "event_time": 2,
        "details": {"symbol": "BTCUSDT"},
    }]


def test_coordinator_can_use_configured_durable_ledger(tmp_path):
    path = str(tmp_path / "coordinator.sqlite3")
    config = ExecutionConfig(state_db_path=path)
    coordinator = ExecutionCoordinator(PaperExecutionAdapter(), config)
    coordinator.submit_intent(intent(), now=3)
    recovered = ExecutionLedger(path)
    assert recovered.snapshot()[-1]["event_type"] == "FILLED"
