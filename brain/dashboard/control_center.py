from __future__ import annotations

from typing import Any
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ControlCenter:
    """Read-only dashboard/API projection of canonical APEX state."""

    def snapshot(self, pipeline_result, paper_position=None) -> dict[str, Any]:
        context = pipeline_result.context
        decision = pipeline_result.decision
        risk = pipeline_result.risk
        return {
            "symbol": context.symbol,
            "price": context.current_price,
            "structure": self._serialize(context.structure),
            "mtf": self._serialize(context.mtf),
            "liquidity": self._serialize(context.liquidity),
            "orderflow": self._serialize(context.orderflow),
            "aggression": self._serialize(context.aggression),
            "absorption": self._serialize(context.absorption),
            "value": self._serialize(context.value),
            "oi": self._serialize(context.oi),
            "fvg": self._serialize(getattr(context, "fvg", None)),
            "order_blocks": self._serialize(getattr(context, "order_blocks", None)),
            "funding": context.funding,
            "effort": self._serialize(context.effort),
            "regime": context.market_regime,
            "setup": self._serialize(context.setup),
            "entry": self._serialize(context.entry),
            "decision": decision.to_dict(),
            "risk": risk.to_dict(),
            "paper_position": self._serialize(paper_position),
            "reason_codes": list(decision.reasons) + list(decision.invalidation),
            "event_time": context.event_time,
            "observability": self._serialize(getattr(context, "observability", None)),
            "data_quality": self._serialize(getattr(context, "data_quality", None)),
            "feed": getattr(context, "metadata", {}).get("feed_continuity", "UNKNOWN"),
        }

    @staticmethod
    def _serialize(value):
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, dict):
            return dict(value)
        return vars(value)


class DashboardService:
    """Read-only API facade backed by one canonical pipeline result provider."""

    _routes = {
        "/market": "snapshot",
        "/snapshot": "snapshot",
        "/decision": "decision",
        "/risk": "risk",
        "/paper-position": "paper_position",
        "/position": "paper_position",
        "/observability": "observability",
        "/data-quality": "data_quality",
        "/feed": "feed",
    }

    def __init__(self, result_provider, paper_position_provider=None) -> None:
        self.result_provider = result_provider
        self.paper_position_provider = paper_position_provider
        self.control_center = ControlCenter()

    def get(self, path: str) -> dict[str, Any]:
        if path == "/health":
            return {"status": "ok", "read_only": True}
        if path not in self._routes:
            if path.rstrip("/") in {"/order", "/orders", "/trade", "/execute", "/cancel", "/position", "/create_order", "/place_order", "/submit_order", "/cancel_order"}:
                raise PermissionError("Live order endpoints are disabled")
            raise KeyError(path)
        result = self.result_provider()
        if result is None:
            return {"status": "unavailable"}
        snapshot = self.control_center.snapshot(result, self.paper_position_provider() if self.paper_position_provider else None)
        key = self._routes[path]
        if key == "data_quality":
            return {key: snapshot.get("data_quality", snapshot.get("observability"))}
        if key == "feed":
            return {key: {"event_time": snapshot.get("event_time"), "symbol": snapshot.get("symbol")}}
        return snapshot if key == "snapshot" else {key: snapshot[key]}


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, service: DashboardService):
        self.service = service

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                try:
                    payload = service.get(handler.path)
                    body = json.dumps(payload, sort_keys=True, default=str).encode()
                    handler.send_response(200)
                except KeyError:
                    body = b'{"error":"not found"}'
                    handler.send_response(404)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)

            def do_POST(handler):
                handler.send_response(403)
                handler.send_header("Content-Length", "0")
                handler.end_headers()

            def log_message(handler, *_args):
                return

        super().__init__(address, Handler)


class DashboardWebSocket:
    """Read-only async update stream; it never invokes pipeline or execution code."""

    def __init__(self, result_provider, paper_position_provider=None):
        self.service = DashboardService(result_provider, paper_position_provider)
        self.connected = False

    async def updates(self, count: int | None = None):
        self.connected = True
        sent = 0
        try:
            while count is None or sent < count:
                yield self.service.get("/snapshot")
                sent += 1
                await asyncio.sleep(0)
        finally:
            self.connected = False

    def receive(self, message: str) -> dict[str, Any]:
        request = json.loads(message)
        if str(request.get("action", "")).lower() in {"order", "execute", "cancel", "trade"}:
            raise PermissionError("Dashboard stream is read-only")
        return self.service.get("/snapshot")


def create_app(result_provider, paper_position_provider=None) -> DashboardService:
    """Create the dependency-free read-only control-center service."""
    return DashboardService(result_provider, paper_position_provider)


def create_http_server(result_provider, paper_position_provider=None, host="127.0.0.1", port=0):
    return DashboardHTTPServer((host, port), create_app(result_provider, paper_position_provider))
