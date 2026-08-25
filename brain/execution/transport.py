from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ExecutionTransportError(RuntimeError):
    def __init__(self, category: str, message: str, *, exchange_code: str | None = None):
        super().__init__(message)
        self.category = category
        self.exchange_code = exchange_code


@dataclass(frozen=True)
class TestnetEndpoint:
    exchange: str
    base_url: str

    def validate(self) -> None:
        allowed = {
            "BINANCE": "https://testnet.binancefuture.com",
            "BYBIT": "https://api-testnet.bybit.com",
        }
        if self.base_url.rstrip("/") != allowed.get(self.exchange.upper()):
            raise ValueError(f"{self.exchange} TESTNET requires its official testnet endpoint")


class AuthenticatedRESTTransport:
    """Injectable signed REST transport for exchange testnet APIs."""

    def __init__(self, exchange: str, api_key: str, api_secret: str, base_url: str, *, recv_window: int = 5000, timeout: float = 10.0, opener: Callable = urlopen, clock: Callable = time.time):
        if not api_key or not api_secret:
            raise ValueError("API credentials are required")
        self.exchange = exchange.upper()
        self.api_key = api_key
        self.api_secret = api_secret
        self.endpoint = TestnetEndpoint(self.exchange, base_url.rstrip("/"))
        self.endpoint.validate()
        self.recv_window = int(recv_window)
        self.timeout = float(timeout)
        self.opener = opener
        self.clock = clock

    def _timestamp(self) -> int:
        return int(self.clock() * 1000)

    def _binance(self, method: str, path: str, params: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        query = {**params, "timestamp": self._timestamp(), "recvWindow": self.recv_window}
        encoded = urlencode(query)
        signature = hmac.new(self.api_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return f"{self.endpoint.base_url}{path}?{encoded}&signature={signature}", {"X-MBX-APIKEY": self.api_key}, b""

    def _bybit(self, method: str, path: str, params: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
        timestamp = str(self._timestamp())
        payload = json.dumps(params, separators=(",", ":"), sort_keys=True) if method.upper() != "GET" else urlencode(params)
        sign_payload = timestamp + self.api_key + str(self.recv_window) + payload
        signature = hmac.new(self.api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "Content-Type": "application/json",
        }
        if method.upper() == "GET":
            return f"{self.endpoint.base_url}{path}?{payload}" if payload else f"{self.endpoint.base_url}{path}", headers, b""
        return f"{self.endpoint.base_url}{path}", headers, payload.encode()

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if self.exchange == "BINANCE":
            url, headers, body = self._binance(method, path, params)
        elif self.exchange == "BYBIT":
            url, headers, body = self._bybit(method, path, params)
        else:
            raise ValueError(f"Unsupported exchange: {self.exchange}")
        request = Request(url, data=body or None, headers=headers, method=method.upper())
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode())
                code = str(payload.get("code", payload.get("retCode", "")))
            except (ValueError, json.JSONDecodeError):
                code = None
            raise ExecutionTransportError("HTTP_ERROR", "Exchange HTTP request failed", exchange_code=code) from None
        except TimeoutError:
            raise ExecutionTransportError("TIMEOUT", "Exchange request timed out") from None
        except URLError:
            raise ExecutionTransportError("NETWORK_ERROR", "Exchange network request failed") from None
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ExecutionTransportError("MALFORMED_RESPONSE", "Exchange returned a malformed response") from None
