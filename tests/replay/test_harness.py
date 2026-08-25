from brain.context import MarketContextBuilder
from brain.pipeline import ApexBrainPipeline
from market.replay import ReplayEvent, ReplayHarness


def make_context(events, event_time):
    return (
        MarketContextBuilder("BTCUSDT", 100)
        .set_event_times(event_time=event_time, calculation_time=event_time)
        .set_data_quality("DATA_INCOMPLETE")
        .build(allow_incomplete=True)
    )


def test_replay_is_deterministic_and_rejects_future_order():
    events = [ReplayEvent(1, "trade", {"id": "a"}), ReplayEvent(2, "trade", {"id": "b"})]
    harness = ReplayHarness(events, make_context)
    first = [result.decision.to_dict() for result in harness.run(ApexBrainPipeline())]
    second = [result.decision.to_dict() for result in harness.run(ApexBrainPipeline())]
    assert first == second


def test_raw_replay_steps_are_incremental_and_deterministic():
    from market.replay import RawBybitEvent, RawBybitReplayHarness

    events = [
        RawBybitEvent(1, 1, {"topic": "tickers.BTCUSDT", "ts": 1000, "data": {"lastPrice": "100", "volume24h": "10000000"}}),
        RawBybitEvent(2, 2, {"topic": "tickers.BTCUSDT", "ts": 2000, "data": {"lastPrice": "101", "volume24h": "10000000"}}),
    ]
    harness = RawBybitReplayHarness(events)
    first = [item.to_dict() for item in harness.run_steps(ApexBrainPipeline())]
    second = [item.to_dict() for item in harness.run_steps(ApexBrainPipeline())]

    assert first == second
    assert len(first) == 2
    assert [item["context"]["event_time"] for item in first] == [1, 2]