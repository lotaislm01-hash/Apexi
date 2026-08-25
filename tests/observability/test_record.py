from types import SimpleNamespace

from brain.decision import BrainDecision, DecisionLevels
from brain.observability import ObservabilityRecorder
from brain.risk import RiskResult


def test_observability_records_final_decision_and_evidence():
    result = SimpleNamespace(
        context=SimpleNamespace(
            symbol="BTCUSDT", event_time=10, market_regime="TRENDING",
            setup=SimpleNamespace(setup_type="TEST"), structure=object(), mtf=object(),
            liquidity=object(), orderflow=object(), value=object(), effort=object(),
        ),
        decision=BrainDecision("WAIT", 0, DecisionLevels(), ["REGIME_CHOP"], ["NO_SETUP"]),
        risk=RiskResult(False, 0, 0, 0),
    )
    record = ObservabilityRecorder().record(result)
    assert record.symbol == "BTCUSDT"
    assert record.event_time == 10
    assert record.setup_type == "TEST"
    assert record.reason_codes == ("REGIME_CHOP",)
    assert record.rejected_reason == ("NO_SETUP",)
    assert record.evidence["effort"] is True