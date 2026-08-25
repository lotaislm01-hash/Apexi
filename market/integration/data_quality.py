from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Iterable


@dataclass(frozen=True)
class QualityResult:
    status: str
    reason_codes: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.status == "VALID"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason_codes": list(self.reason_codes), "details": dict(self.details)}


class DataQualityEngine:
    """Fail-closed validation for raw market observations at an event cutoff."""

    def validate_event(self, event: dict[str, Any], *, as_of: float | None = None, symbol: str | None = None, timeframe: str | None = None) -> QualityResult:
        reasons: list[str] = []
        event_time = event.get("event_time", event.get("timestamp", event.get("T")))
        try:
            event_time = float(event_time)
            if not isfinite(event_time):
                reasons.append("INVALID_TIMESTAMP")
        except (TypeError, ValueError):
            event_time = None
            reasons.append("INVALID_TIMESTAMP")
        if as_of is not None and event_time is not None and event_time > as_of:
            return QualityResult("FUTURE", ("FUTURE_EVENT",), {"event_time": event_time, "as_of": as_of})
        observed_symbol = event.get("symbol")
        if symbol and observed_symbol and str(observed_symbol).upper() != symbol.upper():
            reasons.append("SYMBOL_MISMATCH")
        if timeframe and event.get("timeframe") and str(event["timeframe"]) != timeframe:
            reasons.append("TIMEFRAME_MISMATCH")
        if event.get("duplicate") or event.get("duplicate_id"):
            return QualityResult("DUPLICATE", ("DUPLICATE_EVENT",))
        kind = str(event.get("kind", event.get("type", ""))).lower()
        quantity = event.get("quantity", event.get("qty", event.get("v")))
        if quantity is not None:
            try:
                if not isfinite(float(quantity)) or float(quantity) <= 0:
                    reasons.append("INVALID_QUANTITY")
            except (TypeError, ValueError):
                reasons.append("INVALID_QUANTITY")
        price = event.get("price", event.get("p"))
        if price is not None:
            try:
                if not isfinite(float(price)) or float(price) <= 0:
                    reasons.append("INVALID_PRICE")
            except (TypeError, ValueError):
                reasons.append("INVALID_PRICE")
        if kind in {"trade", "publictrade"} and (price is None or quantity is None or str(event.get("side", event.get("S", ""))).upper() not in {"BUY", "SELL"}):
            reasons.append("MALFORMED_TRADE")
        if all(name in event for name in ("open", "high", "low", "close")):
            try:
                opening, high, low, close = (float(event[name]) for name in ("open", "high", "low", "close"))
                if min(opening, high, low, close) <= 0 or high < low or low > min(opening, close) or high < max(opening, close):
                    reasons.append("INVALID_OHLC")
            except (TypeError, ValueError):
                reasons.append("INVALID_OHLC")
        if event.get("interval") is not None:
            try:
                if float(event["interval"]) <= 0:
                    reasons.append("INVALID_CANDLE_INTERVAL")
            except (TypeError, ValueError):
                reasons.append("INVALID_CANDLE_INTERVAL")
        if event.get("best_bid") is not None and event.get("best_ask") is not None:
            try:
                if float(event["best_bid"]) > float(event["best_ask"]):
                    reasons.append("CROSSED_ORDER_BOOK")
            except (TypeError, ValueError):
                reasons.append("INVALID_PRICE")
        if event.get("sequence_gap"):
            reasons.append("INVALID_SEQUENCE")
        if event.get("sequence_regression") or event.get("out_of_order"):
            reasons.append("SEQUENCE_REGRESSION")
        if event.get("conflicting_snapshot"):
            reasons.append("CONFLICTING_SNAPSHOT")
        if event.get("reconnect"):
            reasons.append("RECONNECT")
        if event.get("missing_snapshot"):
            reasons.append("MISSING_SNAPSHOT")
        if kind in {"orderbook", "book", "order_book"}:
            bids = event.get("bids", ())
            asks = event.get("asks", ())
            if not bids or not asks:
                reasons.append("INVALID_ORDER_BOOK")
            try:
                bid_prices = [float(level[0]) for level in bids]
                ask_prices = [float(level[0]) for level in asks]
                if max(bid_prices) >= min(ask_prices):
                    reasons.append("CROSSED_BOOK")
            except (TypeError, ValueError, IndexError):
                reasons.append("INVALID_ORDER_BOOK")
        if kind in {"oi", "openinterest", "open_interest", "funding"}:
            value = event.get("value", event.get("open_interest", event.get("funding_rate")))
            try:
                if value is None or not isfinite(float(value)) or (kind != "funding" and float(value) < 0):
                    reasons.append("INVALID_VALUE")
            except (TypeError, ValueError):
                reasons.append("INVALID_VALUE")
        if event.get("missing"):
            return QualityResult("INCOMPLETE", ("MISSING_DATA",))
        if event.get("stale"):
            return QualityResult("STALE", ("STALE_DATA",))
        if reasons:
            return QualityResult("INVALID", tuple(dict.fromkeys(reasons)))
        return QualityResult("VALID")

    def validate(self, events: Iterable[dict[str, Any]], *, as_of: float | None = None, symbol: str | None = None, timeframe: str | None = None) -> list[QualityResult]:
        results: list[QualityResult] = []
        seen: set[Any] = set()
        previous_time: float | None = None
        for event in events:
            key = event.get("id", event.get("trade_id", event.get("sequence")))
            result = self.validate_event(event, as_of=as_of, symbol=symbol, timeframe=timeframe)
            if key is not None and key in seen:
                result = QualityResult("DUPLICATE", ("DUPLICATE_EVENT",))
            elif key is not None:
                seen.add(key)
            event_time = event.get("event_time", event.get("timestamp", event.get("T")))
            try:
                current_time = float(event_time)
            except (TypeError, ValueError):
                current_time = None
            if previous_time is not None and current_time is not None and current_time < previous_time:
                status = result.status if result.status == "DUPLICATE" else "INVALID"
                result = QualityResult(status, tuple(dict.fromkeys((*result.reason_codes, "NON_MONOTONIC_EVENT_SEQUENCE"))))
            if current_time is not None:
                previous_time = current_time
            results.append(result)
        return results