from .intent import (
    ExecutionIntent,
    ExecutionIntentBuilder,
)
from .paper import PaperExecutionEngine, PaperPosition
from .adapter import ExchangeExecutionAdapter, InMemoryExecutionAdapter
from .adapters import BinanceExecutionAdapter, BybitExecutionAdapter, CredentialError
from .coordinator import ExecutionCoordinator, ExecutionOutcome
from .model import ExecutionConfig, ExecutionMode, OrderRequest, OrderStatus, PositionSnapshot, ReconciliationResult, deterministic_client_order_id
from .protection import ProtectionManager, ProtectionResult
from .lifecycle import ExecutionEvent, ExecutionLedger
from .paper_adapter import PaperExecutionAdapter


class LiveExecutionDisabled(RuntimeError):
    """Raised whenever live order execution is attempted.

    APEX currently operates in PAPER_ONLY mode.
    Live exchange execution is intentionally unavailable.
    """


class ExecutionEngine:
    """Backward-compatible execution facade.

    This preserves the original APEX Brain API while the newer
    ExecutionIntentBuilder handles paper execution intents.
    """

    def __init__(self, *args, **kwargs):
        self.live_enabled = False

    def execute_live(self, *args, **kwargs):
        raise LiveExecutionDisabled(
            "Live execution is disabled. "
            "APEX currently supports PAPER_ONLY execution."
        )


__all__ = [
    "ExecutionIntent",
    "ExecutionIntentBuilder",
    "ExecutionEngine",
    "LiveExecutionDisabled",
    "PaperExecutionEngine",
    "PaperPosition",
    "ExchangeExecutionAdapter",
    "InMemoryExecutionAdapter",
    "BinanceExecutionAdapter",
    "BybitExecutionAdapter",
    "CredentialError",
    "ExecutionCoordinator",
    "ExecutionOutcome",
    "ExecutionConfig",
    "ExecutionMode",
    "OrderRequest",
    "OrderStatus",
    "PositionSnapshot",
    "ReconciliationResult",
    "deterministic_client_order_id",
    "ProtectionManager",
    "ProtectionResult",
    "ExecutionEvent",
    "ExecutionLedger",
    "PaperExecutionAdapter",
]
