import json
from urllib.error import HTTPError

import pytest

from brain.execution import (
    AuthenticatedRESTTransport,
    BinanceExecutionAdapter,
    BybitExecutionAdapter,
    ExecutionConfig,
    ExecutionMode,
    ExecutionTransportError,
    OrderRequest,
    OrderStateMachine,
    OrderStatus,
)
from tests.execution.test_p3_foundation import intent


def test_binance_transport_signs_testnet_request_without_secret_leak():
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b'{"assets": []}'

    def opener(request, timeout):
        captured.update(url=request.full_url, headers=dict(request.headers), timeout=timeout)
        return Response()

    transport = AuthenticatedRESTTransport("BINANCE", "key", "secret", "https://testnet.binancefuture.com", opener=opener, clock=lambda: 1.0)
    assert transport.request("GET", "/fapi/v2/account") == {"assets": []}
    assert captured["url"].startswith("https://testnet.binancefuture.com/")
    assert "secret" not in captured["url"]
    assert captured["headers"]["X-mbx-apikey"] == "key"


def test_bybit_transport_signs_json_request_and_rejects_production_endpoint():
    transport = AuthenticatedRESTTransport("BYBIT", "key", "secret", "https://api-testnet.bybit.com", clock=lambda: 1.0)
    assert transport.endpoint.base_url == "https://api-testnet.bybit.com"
    with pytest.raises(ValueError):
        AuthenticatedRESTTransport("BYBIT", "key", "secret", "https://api.bybit.com")


def test_exchange_specific_order_schemas_and_bybit_response_normalization():
    config = ExecutionConfig(mode=ExecutionMode.TESTNET, credentials={"api_key": "key", "api_secret": "secret"})
    binance = BinanceExecutionAdapter(config)
    bybit = BybitExecutionAdapter(config)
    order = OrderRequest.from_intent(intent(), exchange="BYBIT", mode=ExecutionMode.TESTNET)
    assert "newClientOrderId" in binance.order_params(order)
    assert "stopPrice" not in binance.order_params(order)
    assert bybit.order_params(order)["orderLinkId"] == order.client_order_id
    assert bybit.order_params(order)["orderType"] == "Market"
    normalized = bybit.normalize_order({"result": {"list": [{
        "symbol": "BTCUSDT", "orderId": "b1", "orderLinkId": "apex-b1",
        "side": "Sell", "orderType": "Market", "qty": "100", "cumExecQty": "30",
        "avgPrice": "101", "orderStatus": "PartiallyFilled",
    }]}})
    assert normalized.client_order_id == "apex-b1"
    assert normalized.status is OrderStatus.PARTIALLY_FILLED
    assert normalized.filled_quantity == 30
    assert BinanceExecutionAdapter(config).normalize_orders({"symbol": "BTCUSDT", "orderId": "1", "clientOrderId": "x", "side": "BUY", "origQty": "1", "status": "NEW"})[0].client_order_id == "x"


def test_transport_normalizes_failures_without_secrets():
    def opener(*_args, **_kwargs):
        raise TimeoutError("secret should not appear")

    transport = AuthenticatedRESTTransport("BINANCE", "key", "secret", "https://testnet.binancefuture.com", opener=opener)
    with pytest.raises(ExecutionTransportError) as error:
        transport.request("GET", "/fapi/v2/account")
    assert error.value.category == "TIMEOUT"
    assert "secret" not in str(error.value)


def test_transport_timeout_enters_unknown_without_blind_resubmission():
    calls = []

    def request(method, path, params):
        calls.append((method, path))
        if path == "/fapi/v2/account":
            return {}
        raise ExecutionTransportError("TIMEOUT", "request timed out")

    config = ExecutionConfig(mode=ExecutionMode.TESTNET, credentials={"api_key": "key", "api_secret": "secret"}, symbol="BTCUSDT")
    adapter = BinanceExecutionAdapter(config, transport=request)
    from brain.execution import ExecutionCoordinator
    outcome = ExecutionCoordinator(adapter, config).submit_intent(intent(), now=1)
    assert outcome.status == "UNKNOWN"
    assert calls == [("GET", "/fapi/v2/account"), ("POST", "/fapi/v1/order"), ("GET", "/fapi/v1/order")]


def test_binance_and_bybit_testnet_methods_use_injected_transport():
    responses = {
        "GET /fapi/v2/account": {"assets": [{"asset": "USDT", "availableBalance": "10"}]},
        "GET /v5/account/wallet-balance": {"result": {"list": [{"coin": [{"coin": "USDT", "availableToWithdraw": "10"}]}]}},
    }

    def request(method, path, params):
        return responses.get(f"{method} {path}", {"symbol": "BTCUSDT", "orderId": "1", "clientOrderId": "x", "side": "BUY", "origQty": "1", "status": "NEW"})

    config = ExecutionConfig(mode=ExecutionMode.TESTNET, credentials={"api_key": "key", "api_secret": "secret"})
    assert BinanceExecutionAdapter(config, transport=request).get_balances()[0]["asset"] == "USDT"
    assert BybitExecutionAdapter(config, transport=request).get_balances()[0]["coin"][0]["coin"] == "USDT"
    order = OrderRequest.from_intent(intent(), exchange="BINANCE", mode=ExecutionMode.TESTNET)
    normalized = BinanceExecutionAdapter(config, transport=request).submit_order(order)
    assert normalized.exchange == "BINANCE"


def test_order_state_machine_rejects_terminal_regressions():
    machine = OrderStateMachine(OrderRequest.from_intent(intent()))
    machine.transition(OrderStatus.ACKNOWLEDGED)
    machine.transition(OrderStatus.FILLED)
    with pytest.raises(ValueError):
        machine.transition(OrderStatus.NEW)


def test_live_adapters_are_disabled_even_with_credentials():
    config = ExecutionConfig(mode=ExecutionMode.LIVE, live_enabled=True, credentials={"api_key": "key", "api_secret": "secret"})
    with pytest.raises(ValueError, match="LIVE execution adapters are disabled"):
        BinanceExecutionAdapter(config)
