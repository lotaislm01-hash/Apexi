from types import SimpleNamespace

from market.backtest import BacktestEngine


def decision():
    return SimpleNamespace(action="LONG", is_trade=True, entry=100, stop_loss=98,
                           levels=SimpleNamespace(tp1=104), setup_type="TEST")


def test_backtest_uses_explicit_stop_first_for_same_bar_ambiguity():
    result = BacktestEngine().run([{"decision": decision(), "high": 105, "low": 97, "regime": "TRENDING"}])
    assert result.losses == 1
    assert result.average_r == -1


def test_backtest_excludes_invalid_missing_and_risk_rejected_records():
    records = [
        {"decision": decision(), "risk_approved": False, "final_price": 104},
        {"decision": decision(), "quantity": 0, "final_price": 104},
        {"decision": decision(), "final_price": 104},
    ]
    assert BacktestEngine().run(records).total_trades == 1