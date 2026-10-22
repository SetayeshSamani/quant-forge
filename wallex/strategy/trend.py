"""Deterministic TP-1 H1 trend eligibility over provisional local candles.

This assessment is neither a candidate entry signal nor an executable intent.
It performs no freshness inference, I/O, wall-clock reads, or order submission.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .indicators import (
    Candle,
    IndicatorArithmeticError,
    InsufficientHistory,
    InvalidCandleData,
    Timeframe,
    ema,
    validate_tp1_warmup,
)

SLOPE_LOOKBACK_BARS = 3


class TrendReason(str, Enum):
    TREND_CONDITIONS_MET = "TREND_CONDITIONS_MET"
    EMA20_NOT_ABOVE_EMA50 = "EMA20_NOT_ABOVE_EMA50"
    EMA20_NOT_RISING_THREE_BARS = "EMA20_NOT_RISING_THREE_BARS"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_DATA = "INVALID_DATA"
    INDICATOR_ARITHMETIC_ERROR = "INDICATOR_ARITHMETIC_ERROR"


@dataclass(frozen=True)
class TrendAssessment:
    """Immutable internal result; eligibility does not authorize an entry.

    Evidence is absent for data/arithmetic failures. Evidence timestamps are UTC
    Unix seconds at candle CLOSE, unlike Candle.timestamp (candle OPEN time).
    """

    eligible: bool
    reasons: tuple[TrendReason, ...]
    latest_closed_at: int | None = None
    slope_reference_closed_at: int | None = None
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema20_three_bars_ago: Decimal | None = None


def assess_hourly_trend(
    candles: Sequence[Candle] | None = None, *, as_of: int
) -> TrendAssessment:
    """Require 250 completed, contiguous H1 bars and both strict TP-1 tests.

    Tests: EMA20[h] > EMA50[h] and EMA20[h] > EMA20[h-3]. The existing
    SMA-seeded EMA supplies the fixed Decimal arithmetic context. Consecutive
    validated H1 bars make h-3 exactly three hours before h, not three EMA points
    inferred across a missing bar. Future or incomplete rows reject the batch;
    callers must supply the completed history prefix for their explicit cutoff.
    """
    try:
        data = validate_tp1_warmup(candles, as_of=as_of)
        fast = ema(data, 20, timeframe=Timeframe.H1, as_of=as_of)
        slow = ema(data, 50, timeframe=Timeframe.H1, as_of=as_of)
    except InsufficientHistory:
        return TrendAssessment(False, (TrendReason.INSUFFICIENT_HISTORY,))
    except InvalidCandleData:
        return TrendAssessment(False, (TrendReason.INVALID_DATA,))
    except IndicatorArithmeticError:
        return TrendAssessment(False, (TrendReason.INDICATOR_ARITHMETIC_ERROR,))

    current_fast = fast[-1]
    current_slow = slow[-1]
    reference_fast = fast[-(SLOPE_LOOKBACK_BARS + 1)]
    reasons = []
    if current_fast.value <= current_slow.value:
        reasons.append(TrendReason.EMA20_NOT_ABOVE_EMA50)
    if current_fast.value <= reference_fast.value:
        reasons.append(TrendReason.EMA20_NOT_RISING_THREE_BARS)
    return TrendAssessment(
        eligible=not reasons,
        reasons=tuple(reasons) or (TrendReason.TREND_CONDITIONS_MET,),
        latest_closed_at=current_fast.closed_at,
        slope_reference_closed_at=reference_fast.closed_at,
        ema20=current_fast.value,
        ema50=current_slow.value,
        ema20_three_bars_ago=reference_fast.value,
    )
