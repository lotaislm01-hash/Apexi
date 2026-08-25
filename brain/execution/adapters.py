from __future__ import annotations

from dataclasses import replace
from typing import Any

from .adapter import InMemoryExecutionAdapter
from .model import ExecutionConfig, ExecutionMode, OrderRequest, OrderStatus


class CredentialError(ValueError):
    pass


class NormalizingExecutionAdapter(InMemoryExecutionAdapter):
    """Exchange adapter foundation with deterministic response normalization."""

    def __init__(self, exchange: str, config: ExecutionConfig | None = None, transport=None) -> None:
        self.config = config or ExecutionConfig()
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE}:
            if not self.config.credentials.get("api_key") or not self.config.credentials.get("api_secret"):
                raise CredentialError(f"{exchange} credentials are required for {self.config.mode.value}")
        super().__init__(exchange)
        self.transport = transport

    def submit_order(self, order):
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE} and self.transport is None:
            raise ConnectionError("network transport is not configured")
        if self.transport is not None:
            response = self.transport("submit_order", order.to_dict())
            return self.normalize_order(response)
        return super().submit_order(order)

    def normalize_order(self, response: dict[str, Any]) -> OrderRequest:
        if not isinstance(response, dict) or not response.get("symbol"):
            raise ValueError("Malformed exchange order response")
        status = str(response.get("status", "NEW")).upper()
        try:
            order_status = OrderStatus(status)
        except ValueError:
            order_status = OrderStatus.UNKNOWN
        return OrderRequest(
            client_order_id=str(response.get("clientOrderId", response.get("client_order_id", ""))),
            exchange_order_id=str(response.get("orderId", response.get("order_id"))) if response.get("orderId", response.get("order_id")) is not None else None,
            symbol=str(response["symbol"]).upper(),
            side=str(response.get("side", "")).upper(),
            order_type=str(response.get("type", response.get("order_type", "MARKET"))).upper(),
            quantity=float(response.get("origQty", response.get("quantity", 0))),
            price=float(response["price"]) if response.get("price") not in (None, "") else None,
            stop_price=float(response["stopPrice"]) if response.get("stopPrice") not in (None, "") else None,
            reduce_only=bool(response.get("reduceOnly", response.get("reduce_only", False))),
            close_position=bool(response.get("closePosition", response.get("close_position", False))),
            leverage=float(response.get("leverage", 1)),
            status=order_status,
            filled_quantity=float(response.get("executedQty", response.get("filled_quantity", 0))),
            average_fill_price=float(response["avgPrice"]) if response.get("avgPrice") not in (None, "") else None,
            exchange=self.exchange,
            execution_mode=self.config.mode,
        )


class BinanceExecutionAdapter(NormalizingExecutionAdapter):
    def __init__(self, config=None, transport=None):
        super().__init__("BINANCE", config, transport)


class BybitExecutionAdapter(NormalizingExecutionAdapter):
    def __init__(self, config=None, transport=None):
        super().__init__("BYBIT", config, transport)


__all__ = ["BinanceExecutionAdapter", "BybitExecutionAdapter", "CredentialError"]
