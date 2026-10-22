"""Proposed initial stop levels for long spot candidates; no trade authorization."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
from enum import Enum

from .indicators import (
    ATR_PERIOD, DECIMAL_PRECISION, Candle, IndicatorArithmeticError,
    InsufficientHistory, InvalidCandleData, Timeframe, atr, validate_candles,
)


class StopReason(str, Enum):
    LEVELS_VALID = "LEVELS_VALID"
    INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"
    INVALID_DATA = "INVALID_DATA"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_ATR14 = "INVALID_ATR14"
    INDICATOR_ALIGNMENT_ERROR = "INDICATOR_ALIGNMENT_ERROR"
    ARITHMETIC_ERROR = "ARITHMETIC_ERROR"
    NON_POSITIVE_STOP = "NON_POSITIVE_STOP"
    STOP_NOT_BELOW_ENTRY = "STOP_NOT_BELOW_ENTRY"
    NON_POSITIVE_RISK_PER_UNIT = "NON_POSITIVE_RISK_PER_UNIT"


@dataclass(frozen=True)
class StopAssessment:
    """Immutable local calculation, distinct from risk approval or an order.

    decision_closed_at is UTC Unix seconds at candle CLOSE. Every failure leaves
    all evidence fields None; unsuccessful calculations never supply usable levels.
    """

    valid: bool
    reasons: tuple[StopReason, ...]
    decision_closed_at: int | None = None
    entry_price: Decimal | None = None
    lowest_low_last3: Decimal | None = None
    atr14: Decimal | None = None
    structural_stop: Decimal | None = None
    volatility_stop: Decimal | None = None
    initial_stop: Decimal | None = None
    risk_per_unit: Decimal | None = None


def _context() -> Context:
    return Context(
        prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN,
        Emin=-999999, Emax=999999, capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def assess_initial_stop(
    candles: Sequence[Candle] | None = None, *, entry_price: Decimal, as_of: int
) -> StopAssessment:
    """Use latest three M15 lows and latest ATR14 to propose the lower stop.

    structural = min(last three lows) - 0.1*ATR
    volatility = entry_price - 1.5*ATR
    initial = min(structural, volatility); risk_per_unit = entry_price - initial

    entry_price is required explicitly and never inferred from a candle. At least
    14 completed contiguous M15 bars are required. No tick-size rounding, wall-clock
    freshness inference, position sizing, risk approval, or order submission occurs.
    """
    if not isinstance(entry_price, Decimal) or not entry_price.is_finite() or entry_price <= 0:
        return StopAssessment(False, (StopReason.INVALID_ENTRY_PRICE,))
    try:
        data = validate_candles(candles, timeframe=Timeframe.M15, as_of=as_of)
        if len(data) < ATR_PERIOD:
            raise InsufficientHistory(ATR_PERIOD, len(data))
        points = atr(data, timeframe=Timeframe.M15, as_of=as_of)
    except InsufficientHistory:
        return StopAssessment(False, (StopReason.INSUFFICIENT_HISTORY,))
    except InvalidCandleData:
        return StopAssessment(False, (StopReason.INVALID_DATA,))
    except IndicatorArithmeticError:
        return StopAssessment(False, (StopReason.ARITHMETIC_ERROR,))

    decision_time = data[-1].timestamp + Timeframe.M15.value
    by_close = {point.closed_at: point.value for point in points}
    if decision_time not in by_close:
        return StopAssessment(False, (StopReason.INDICATOR_ALIGNMENT_ERROR,))
    current_atr = by_close[decision_time]
    if not isinstance(current_atr, Decimal) or not current_atr.is_finite() or current_atr <= 0:
        return StopAssessment(False, (StopReason.INVALID_ATR14,))
    lowest_low = min(candle.low for candle in data[-3:])
    try:
        with localcontext(_context()):
            structural = lowest_low - Decimal("0.1") * current_atr
            volatility = entry_price - Decimal("1.5") * current_atr
            initial = min(structural, volatility)
            risk = entry_price - initial
    except DecimalException:
        return StopAssessment(False, (StopReason.ARITHMETIC_ERROR,))
    if not all(value.is_finite() for value in (structural, volatility, initial, risk)):
        return StopAssessment(False, (StopReason.ARITHMETIC_ERROR,))
    reasons = []
    if initial <= 0:
        reasons.append(StopReason.NON_POSITIVE_STOP)
    if initial >= entry_price:
        reasons.append(StopReason.STOP_NOT_BELOW_ENTRY)
    if risk <= 0:
        reasons.append(StopReason.NON_POSITIVE_RISK_PER_UNIT)
    if reasons:
        return StopAssessment(False, tuple(reasons))
    return StopAssessment(
        valid=True,
        reasons=(StopReason.LEVELS_VALID,),
        decision_closed_at=decision_time,
        entry_price=entry_price,
        lowest_low_last3=lowest_low,
        atr14=current_atr,
        structural_stop=structural,
        volatility_stop=volatility,
        initial_stop=initial,
        risk_per_unit=risk,
    )
