from market.integration.data_quality import DataQualityEngine


def test_data_quality_covers_invalid_future_duplicate_sequence_and_symbol():
    engine = DataQualityEngine()
    assert engine.validate_event({"event_time": 11}, as_of=10).status == "FUTURE"
    assert engine.validate_event({"event_time": 1, "symbol": "ETHUSDT"}, symbol="BTCUSDT").status == "INVALID"
    assert engine.validate_event({"event_time": 1, "price": 0, "quantity": -1}).status == "INVALID"
    results = engine.validate([{"id": "a", "event_time": 2}, {"id": "a", "event_time": 1}])
    assert results[1].status == "DUPLICATE"
    assert "NON_MONOTONIC_EVENT_SEQUENCE" in engine.validate([{"event_time": 2}, {"event_time": 1}])[1].reason_codes


def test_data_quality_covers_trade_ohlc_book_interval_missing_and_stale():
    engine = DataQualityEngine()
    assert "MALFORMED_TRADE" in engine.validate_event({"kind": "trade", "event_time": 1, "price": 2, "quantity": 1}).reason_codes
    assert "INVALID_OHLC" in engine.validate_event({"event_time": 1, "open": 2, "high": 1, "low": 3, "close": 2}).reason_codes
    assert "CROSSED_ORDER_BOOK" in engine.validate_event({"event_time": 1, "best_bid": 3, "best_ask": 2}).reason_codes
    assert "INVALID_CANDLE_INTERVAL" in engine.validate_event({"event_time": 1, "interval": 0}).reason_codes
    assert engine.validate_event({"missing": True}).status == "INCOMPLETE"
    assert engine.validate_event({"stale": True}).status == "STALE"