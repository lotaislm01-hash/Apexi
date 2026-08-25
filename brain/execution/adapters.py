from __future__ import annotations

from dataclasses import replace
from typing import Any

from .adapter import InMemoryExecutionAdapter
from .model import ExecutionConfig, ExecutionMode, OrderRequest, OrderStatus, PositionSnapshot, ReconciliationResult
from .transport import AuthenticatedRESTTransport


class CredentialError(ValueError):
    pass


class NormalizingExecutionAdapter(InMemoryExecutionAdapter):
    """Exchange adapter foundation with deterministic response normalization."""

    def __init__(self, exchange: str, config: ExecutionConfig | None = None, transport=None) -> None:
        self.config = config or ExecutionConfig()
        if self.config.mode is ExecutionMode.LIVE:
            raise CredentialError("LIVE execution adapters are disabled in P3.2")
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE}:
            if not self.config.credentials.get("api_key") or not self.config.credentials.get("api_secret"):
                raise CredentialError(f"{exchange} credentials are required for {self.config.mode.value}")
        super().__init__(exchange)
        if transport is None and self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE}:
            default_url = "https://testnet.binancefuture.com" if exchange == "BINANCE" else "https://api-testnet.bybit.com"
            transport = AuthenticatedRESTTransport(
                exchange,
                self.config.credentials["api_key"],
                self.config.credentials["api_secret"],
                self.config.base_url or default_url,
                recv_window=self.config.recv_window,
                timeout=self.config.timeout,
            )
        self.transport = transport

    def submit_order(self, order):
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE} and self.transport is None:
            raise ConnectionError("network transport is not configured")
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE} and self.transport is not None:
            response = self.transport("submit_order", order.to_dict())
            return self.normalize_order(response)
        return super().submit_order(order)

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None):
        if self.transport is None:
            raise ConnectionError("network transport is not configured")
        request = self.transport.request if hasattr(self.transport, "request") else self.transport
        return request(method, path, params or {})

    def _testnet_request(self, method: str, path: str, params: dict[str, Any] | None = None):
        return self._request(method, path, params)

    def get_account_state(self):
        if self.config.mode in {ExecutionMode.PAPER, ExecutionMode.SHADOW}:
            return super().get_account_state()
        payload = self._testnet_request("GET", self.account_path)
        return self.normalize_account(payload)

    def get_balances(self):
        if self.config.mode in {ExecutionMode.PAPER, ExecutionMode.SHADOW}:
            return super().get_balances()
        return self.normalize_balances(self._testnet_request("GET", self.account_path))

    def get_positions(self):
        payload = self._testnet_request("GET", self.position_path)
        return self.normalize_positions(payload)

    def get_open_orders(self):
        payload = self._testnet_request("GET", self.open_orders_path)
        return self.normalize_orders(payload)

    def get_instrument_metadata(self, symbol):
        return {"symbol": symbol.upper()}

    def submit_order(self, order):
        if self.config.mode in {ExecutionMode.TESTNET, ExecutionMode.LIVE}:
            if self.transport is None:
                raise ConnectionError("network transport is not configured")
            normalized = self.normalize_order(self._testnet_request("POST", self.order_path, self.order_params(order)))
            if normalized.status in {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED} and not order.reduce_only:
                self.positions = [PositionSnapshot(normalized.symbol, "LONG" if normalized.side == "BUY" else "SHORT", normalized.filled_quantity, normalized.average_fill_price, self.exchange)]
            return normalized
        return super().submit_order(order)

    def get_order(self, client_order_id):
        payload = self._testnet_request("GET", self.order_query_path, self.order_query_params(client_order_id))
        orders = self.normalize_orders(payload)
        return orders[0] if orders else None

    def cancel_order(self, client_order_id):
        return self.normalize_order(self._testnet_request("DELETE", self.cancel_path, self.order_query_params(client_order_id)))

    def amend_order(self, order):
        return self.normalize_order(self._testnet_request("PUT", self.amend_path, self.order_params(order)))

    def order_query_params(self, client_order_id):
        return {"origClientOrderId": client_order_id}

    def normalize_account(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Malformed exchange account response")
        return payload

    def normalize_balances(self, payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("assets"), list):
            return payload["assets"]
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return self.normalize_balances(payload["result"])
        if isinstance(payload, dict) and isinstance(payload.get("list"), list):
            return payload["list"]
        raise ValueError("Malformed exchange balance response")

    def normalize_positions(self, payload):
        values = payload if isinstance(payload, list) else payload.get("positions", payload.get("result", [])) if isinstance(payload, dict) else []
        if isinstance(values, dict):
            values = values.get("list", [])
        if not isinstance(values, list):
            raise ValueError("Malformed exchange position response")
        return [PositionSnapshot(str(item.get("symbol", "")).upper(), "LONG" if float(item.get("positionAmt", item.get("size", 0))) > 0 else "SHORT", abs(float(item.get("positionAmt", item.get("size", 0)))), float(item.get("entryPrice", item.get("avgPrice", 0))) or None, self.exchange) for item in values if float(item.get("positionAmt", item.get("size", 0))) != 0]

    def normalize_orders(self, payload):
        if isinstance(payload, dict) and payload.get("symbol"):
            return [self.normalize_order(payload)]
        values = payload if isinstance(payload, list) else payload.get("orders", payload.get("result", [])) if isinstance(payload, dict) else []
        if isinstance(values, dict):
            values = values.get("list", [])
        if not isinstance(values, list):
            raise ValueError("Malformed exchange order response")
        return [self.normalize_order(item) for item in values]

    def order_params(self, order):
        return {"symbol": order.symbol, "side": order.side, "type": order.order_type, "quantity": order.quantity, "price": order.price, "stopPrice": order.stop_price, "newClientOrderId": order.client_order_id, "reduceOnly": order.reduce_only, "closePosition": order.close_position}

    def normalize_order(self, response: dict[str, Any]) -> OrderRequest:
        if not isinstance(response, dict) or not response.get("symbol"):
            raise ValueError("Malformed exchange order response")
        status = str(response.get("status", "NEW")).upper()
        try:
            order_status = OrderStatus(status)
        except ValueError:
            order_status = OrderStatus.UNKNOWN
        def number(name: str, fallback: str | None = None):
            value = response.get(name, fallback)
            return None if value in (None, "", "None", "null") else float(value)

        return OrderRequest(
            client_order_id=str(response.get("clientOrderId", response.get("client_order_id", ""))),
            exchange_order_id=str(response.get("orderId", response.get("order_id"))) if response.get("orderId", response.get("order_id")) is not None else None,
            symbol=str(response["symbol"]).upper(),
            side=str(response.get("side", "")).upper(),
            order_type=str(response.get("type", response.get("order_type", "MARKET"))).upper(),
            quantity=float(response.get("origQty", response.get("quantity", 0))),
            price=number("price"),
            stop_price=number("stopPrice"),
            reduce_only=bool(response.get("reduceOnly", response.get("reduce_only", False))),
            close_position=bool(response.get("closePosition", response.get("close_position", False))),
            leverage=float(response.get("leverage", 1)),
            status=order_status,
            filled_quantity=float(response.get("executedQty", response.get("filled_quantity", 0))),
            average_fill_price=number("avgPrice"),
            exchange=self.exchange,
            execution_mode=self.config.mode,
        )


class BinanceExecutionAdapter(NormalizingExecutionAdapter):
    account_path = "/fapi/v2/account"
    balance_path = "/fapi/v2/balance"
    position_path = "/fapi/v2/positionRisk"
    open_orders_path = "/fapi/v1/openOrders"
    order_path = "/fapi/v1/order"
    order_query_path = "/fapi/v1/order"
    cancel_path = "/fapi/v1/order"
    amend_path = "/fapi/v1/order"
    def __init__(self, config=None, transport=None):
        super().__init__("BINANCE", config, transport)

    def order_params(self, order):
        order_type = "TAKE_PROFIT_MARKET" if order.order_type == "TAKE_PROFIT" and order.close_position else order.order_type
        params = {"symbol": order.symbol, "side": order.side, "type": order_type, "newClientOrderId": order.client_order_id}
        if not order.close_position:
            params["quantity"] = order.quantity
            params["reduceOnly"] = str(order.reduce_only).lower()
        if order.price is not None and order.order_type != "MARKET":
            params["price"] = order.price
            params["timeInForce"] = "GTC"
        if order.stop_price is not None:
            params["stopPrice"] = order.stop_price
        if order.close_position:
            params["closePosition"] = "true"
        return params

    def order_query_params(self, client_order_id):
        return {"symbol": self.config.symbol or "BTCUSDT", "origClientOrderId": client_order_id}


class BybitExecutionAdapter(NormalizingExecutionAdapter):
    account_path = "/v5/account/wallet-balance"
    position_path = "/v5/position/list"
    open_orders_path = "/v5/order/realtime"
    order_path = "/v5/order/create"
    order_query_path = "/v5/order/realtime"
    cancel_path = "/v5/order/cancel"
    amend_path = "/v5/order/amend"
    def __init__(self, config=None, transport=None):
        super().__init__("BYBIT", config, transport)

    def order_params(self, order):
        params = {"category": "linear", "symbol": order.symbol, "side": order.side.title(), "orderType": order.order_type.title(), "qty": str(order.quantity), "orderLinkId": order.client_order_id, "reduceOnly": order.reduce_only, "closeOnTrigger": order.close_position}
        if order.price is not None and order.order_type != "MARKET":
            params["price"] = str(order.price)
        if order.stop_price is not None:
            params["triggerPrice"] = str(order.stop_price)
        return params

    def order_query_params(self, client_order_id):
        return {"category": "linear", "symbol": self.config.symbol or "BTCUSDT", "orderLinkId": client_order_id}

    def normalize_order(self, response: dict[str, Any]) -> OrderRequest:
        if isinstance(response.get("result"), dict):
            values = response["result"].get("list", [])
            if values:
                response = values[0]
        mapped = {
            "symbol": response.get("symbol"),
            "orderId": response.get("orderId"),
            "clientOrderId": response.get("orderLinkId", response.get("clientOrderId")),
            "side": response.get("side"),
            "type": response.get("orderType", response.get("type", "Market")).upper(),
            "origQty": response.get("qty", response.get("origQty", 0)),
            "executedQty": response.get("cumExecQty", response.get("executedQty", 0)),
            "avgPrice": response.get("avgPrice"),
            "status": str(response.get("orderStatus", response.get("status", "NEW"))).upper().replace("PARTIALLYFILLED", "PARTIALLY_FILLED").replace("CANCELLED", "CANCELED"),
            "price": response.get("price"),
            "stopPrice": response.get("triggerPrice"),
            "reduceOnly": response.get("reduceOnly", False),
            "closePosition": response.get("closeOnTrigger", False),
        }
        return super().normalize_order(mapped)

    def normalize_positions(self, payload):
        values = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
        if not isinstance(values, list):
            raise ValueError("Malformed Bybit position response")
        result = []
        for item in values:
            size = float(item.get("size", 0))
            if size:
                result.append(PositionSnapshot(str(item.get("symbol", "")).upper(), str(item.get("side", "")).upper(), size, float(item.get("avgPrice", 0)) or None, self.exchange))
        return result


__all__ = ["BinanceExecutionAdapter", "BybitExecutionAdapter", "CredentialError"]
