from __future__ import annotations

from dataclasses import dataclass

from .model import ExecutionMode, OrderRequest


@dataclass(frozen=True)
class ProtectionResult:
    verified: bool
    stop_loss: float | None
    take_profit: float | None
    reason: str | None = None

    def to_dict(self):
        return dict(vars(self))


class ProtectionManager:
    """Validate and verify protective orders without assuming exchange success."""

    def create_plan(self, intent, *, exchange: str = "PAPER", mode: ExecutionMode = ExecutionMode.PAPER) -> tuple[OrderRequest, ...]:
        if not intent.approved:
            raise ValueError("Protection requires an approved intent")
        if intent.action == "LONG" and not intent.stop_loss < intent.entry:
            raise ValueError("Long stop-loss must be below entry")
        if intent.action == "SHORT" and not intent.stop_loss > intent.entry:
            raise ValueError("Short stop-loss must be above entry")
        entry = OrderRequest.from_intent(intent, exchange=exchange, mode=mode)
        orders = [entry]
        orders.append(OrderRequest(
            client_order_id=f"{entry.client_order_id}-sl", exchange_order_id=None,
            symbol=intent.symbol, side="SELL" if intent.action == "LONG" else "BUY",
            order_type="STOP_MARKET", quantity=float(intent.quantity), stop_price=float(intent.stop_loss),
            reduce_only=True, close_position=True, leverage=float(intent.leverage),
            exchange=exchange, execution_mode=mode, parent_client_order_id=entry.client_order_id,
        ))
        for index, target in enumerate((intent.tp1, intent.tp2, intent.tp3), start=1):
            if target is None:
                continue
            orders.append(OrderRequest(
                client_order_id=f"{entry.client_order_id}-tp{index}", exchange_order_id=None,
                symbol=intent.symbol, side="SELL" if intent.action == "LONG" else "BUY",
                order_type="TAKE_PROFIT", quantity=float(intent.quantity), price=float(target),
                reduce_only=True, close_position=True, leverage=float(intent.leverage),
                exchange=exchange, execution_mode=mode,
                parent_client_order_id=entry.client_order_id,
            ))
        return tuple(orders)

    def verify(self, entry: OrderRequest, protection_orders) -> ProtectionResult:
        stops = [order for order in protection_orders if order.stop_price is not None]
        targets = [order for order in protection_orders if order.order_type == "TAKE_PROFIT"]
        if not stops or not targets:
            return ProtectionResult(False, None, None, "PROTECTION_MISSING")
        return ProtectionResult(True, stops[0].stop_price, targets[0].price)
