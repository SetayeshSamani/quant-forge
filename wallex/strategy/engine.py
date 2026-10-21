"""Deterministic TP-1 interface scaffold. No strategy calculations or signals yet."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Signal(str, Enum):
    NO_TRADE = "NO_TRADE"


class DecisionReason(str, Enum):
    MISSING_DATA = "MISSING_DATA"
    INVALID_INPUT = "INVALID_INPUT"
    CALCULATIONS_NOT_IMPLEMENTED = "CALCULATIONS_NOT_IMPLEMENTED"


@dataclass(frozen=True)
class StrategyInput:
    """Internal placeholder, not a shared market-data contract.

    Prices must be supplied by the caller. No prices are generated or fetched.
    This deliberately incomplete snapshot cannot establish a TP-1 signal.
    """

    symbol: str = ""
    close_prices: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class StrategyDecision:
    signal: Signal
    reason: DecisionReason


def evaluate(inputs: StrategyInput | None = None) -> StrategyDecision:
    """Return NO_TRADE for missing, malformed, and currently valid inputs alike."""
    if inputs is None:
        return StrategyDecision(Signal.NO_TRADE, DecisionReason.MISSING_DATA)
    if not isinstance(inputs, StrategyInput):
        return StrategyDecision(Signal.NO_TRADE, DecisionReason.INVALID_INPUT)
    if not isinstance(inputs.symbol, str) or not isinstance(inputs.close_prices, tuple):
        return StrategyDecision(Signal.NO_TRADE, DecisionReason.INVALID_INPUT)
    if not inputs.symbol.strip() or not inputs.close_prices:
        return StrategyDecision(Signal.NO_TRADE, DecisionReason.MISSING_DATA)
    for price in inputs.close_prices:
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return StrategyDecision(Signal.NO_TRADE, DecisionReason.INVALID_INPUT)
    return StrategyDecision(
        Signal.NO_TRADE, DecisionReason.CALCULATIONS_NOT_IMPLEMENTED
    )
