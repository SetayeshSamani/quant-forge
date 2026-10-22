"""M15 pullback eligibility only; no approved signal, agent, risk sizing, or orders."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
from enum import Enum

from .indicators import (
    DECIMAL_PRECISION, Candle, IndicatorArithmeticError, InsufficientHistory,
    InvalidCandleData, Timeframe, atr, ema, validate_candles,
)

MIN_PULLBACK_HISTORY = 21


class PullbackReason(str, Enum):
    PULLBACK_CONDITIONS_MET = "PULLBACK_CONDITIONS_MET"
    PREVIOUS_CLOSE_ABOVE_EMA20 = "PREVIOUS_CLOSE_ABOVE_EMA20"
    CURRENT_CLOSE_NOT_ABOVE_EMA20 = "CURRENT_CLOSE_NOT_ABOVE_EMA20"
    ATR14_NOT_POSITIVE = "ATR14_NOT_POSITIVE"
    DISTANCE_NOT_POSITIVE = "DISTANCE_NOT_POSITIVE"
    DISTANCE_EXCEEDS_ATR14 = "DISTANCE_EXCEEDS_ATR14"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_DATA = "INVALID_DATA"
    INDICATOR_ARITHMETIC_ERROR = "INDICATOR_ARITHMETIC_ERROR"
    INDICATOR_ALIGNMENT_ERROR = "INDICATOR_ALIGNMENT_ERROR"


@dataclass(frozen=True)
class PullbackAssessment:
    """Internal immutable evidence, not a CandidateSignal or TradeIntent.

    Timestamps are UTC Unix seconds at candle CLOSE. Data/arithmetic/alignment
    failures carry no indicator evidence. Valid nonqualifying data retains its
    actual calculated evidence, including zero ATR or a nonpositive distance.
    """

    eligible: bool
    reasons: tuple[PullbackReason, ...]
    decision_closed_at: int | None = None
    previous_closed_at: int | None = None
    current_close: Decimal | None = None
    previous_close: Decimal | None = None
    current_ema20: Decimal | None = None
    previous_ema20: Decimal | None = None
    current_atr14: Decimal | None = None
    distance_from_ema20: Decimal | None = None


def _context() -> Context:
    # Match the existing indicator precision without inheriting caller settings.
    return Context(
        prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN,
        Emin=-999999, Emax=999999, capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def assess_pullback(
    candles: Sequence[Candle] | None = None, *, as_of: int
) -> PullbackAssessment:
    """Require an upward M15 EMA20 cross with 0 < close-EMA20 <= ATR14.

    Previous close <= previous EMA20 is inclusive; current close > current EMA20
    is strict. At least 21 valid completed contiguous M15 bars are required.
    Existing indicator results are selected by candle CLOSE time, not by assuming
    identical EMA/ATR warm-up lengths. Full strategy eligibility also needs the
    separate H1 trend assessment; this function never evaluates or authorizes it.
    """
    try:
        data = validate_candles(candles, timeframe=Timeframe.M15, as_of=as_of)
        if len(data) < MIN_PULLBACK_HISTORY:
            raise InsufficientHistory(MIN_PULLBACK_HISTORY, len(data))
        ema_points = ema(data, 20, timeframe=Timeframe.M15, as_of=as_of)
        atr_points = atr(data, timeframe=Timeframe.M15, as_of=as_of)
    except InsufficientHistory:
        return PullbackAssessment(False, (PullbackReason.INSUFFICIENT_HISTORY,))
    except InvalidCandleData:
        return PullbackAssessment(False, (PullbackReason.INVALID_DATA,))
    except IndicatorArithmeticError:
        return PullbackAssessment(False, (PullbackReason.INDICATOR_ARITHMETIC_ERROR,))

    current, previous = data[-1], data[-2]
    current_time = current.timestamp + Timeframe.M15.value
    previous_time = previous.timestamp + Timeframe.M15.value
    ema_by_close = {point.closed_at: point.value for point in ema_points}
    atr_by_close = {point.closed_at: point.value for point in atr_points}
    try:
        current_ema = ema_by_close[current_time]
        previous_ema = ema_by_close[previous_time]
        current_atr = atr_by_close[current_time]
    except KeyError:
        return PullbackAssessment(False, (PullbackReason.INDICATOR_ALIGNMENT_ERROR,))
    if not all(isinstance(value, Decimal) and value.is_finite()
               for value in (current_ema, previous_ema, current_atr)):
        return PullbackAssessment(False, (PullbackReason.INDICATOR_ARITHMETIC_ERROR,))
    if current_ema <= 0 or previous_ema <= 0 or current_atr < 0:
        return PullbackAssessment(False, (PullbackReason.INDICATOR_ARITHMETIC_ERROR,))

    try:
        with localcontext(_context()):
            distance = current.close - current_ema
            reasons = []
            if previous.close > previous_ema:
                reasons.append(PullbackReason.PREVIOUS_CLOSE_ABOVE_EMA20)
            if current.close <= current_ema:
                reasons.append(PullbackReason.CURRENT_CLOSE_NOT_ABOVE_EMA20)
            if current_atr <= 0:
                reasons.append(PullbackReason.ATR14_NOT_POSITIVE)
            if distance <= 0:
                reasons.append(PullbackReason.DISTANCE_NOT_POSITIVE)
            elif distance > current_atr:
                reasons.append(PullbackReason.DISTANCE_EXCEEDS_ATR14)
    except DecimalException:
        return PullbackAssessment(False, (PullbackReason.INDICATOR_ARITHMETIC_ERROR,))
    return PullbackAssessment(
        eligible=not reasons,
        reasons=tuple(reasons) or (PullbackReason.PULLBACK_CONDITIONS_MET,),
        decision_closed_at=current_time,
        previous_closed_at=previous_time,
        current_close=current.close,
        previous_close=previous.close,
        current_ema20=current_ema,
        previous_ema20=previous_ema,
        current_atr14=current_atr,
        distance_from_ema20=distance,
    )
