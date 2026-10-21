"""Pure in-memory runtime states. No workers, exchange clients, or execution."""

from dataclasses import dataclass
from enum import Enum


class RuntimeState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


class LiveTradingDisabledError(RuntimeError):
    """Raised for every real-order execution attempt in this foundation."""


@dataclass(frozen=True)
class Runtime:
    """Immutable state model; RUNNING is only a label, not a trading worker."""

    state: RuntimeState = RuntimeState.STOPPED

    def __post_init__(self) -> None:
        if not isinstance(self.state, RuntimeState):
            raise ValueError("state must be a RuntimeState")

    @property
    def live_trading_enabled(self) -> bool:
        return False

    def transition(self, target: RuntimeState) -> "Runtime":
        """Validate a transition and return a new model without starting work."""
        if not isinstance(target, RuntimeState):
            raise ValueError("target must be a RuntimeState")
        allowed = {
            RuntimeState.STOPPED: (RuntimeState.STARTING,),
            RuntimeState.STARTING: (
                RuntimeState.RUNNING, RuntimeState.DEGRADED, RuntimeState.STOPPING
            ),
            RuntimeState.RUNNING: (RuntimeState.DEGRADED, RuntimeState.STOPPING),
            RuntimeState.DEGRADED: (RuntimeState.RUNNING, RuntimeState.STOPPING),
            RuntimeState.STOPPING: (RuntimeState.STOPPED,),
        }
        if target not in allowed[self.state]:
            raise ValueError(f"Invalid transition: {self.state.value} -> {target.value}")
        return Runtime(target)

    def execute_order(self, order: object = None) -> None:
        """Always refuse; the payload is never inspected, forwarded, or called."""
        raise LiveTradingDisabledError("Live order execution is disabled")
