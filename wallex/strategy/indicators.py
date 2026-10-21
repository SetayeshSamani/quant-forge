"""Causal Decimal indicators over provisional, completed UTC candle records.

Timestamps are candle OPEN times in Unix seconds. Every public series operation
requires a timeframe and an explicit as_of cutoff; no wall clock is consulted.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
from enum import Enum

DECIMAL_PRECISION = 34
ATR_PERIOD = 14
TP1_HOURLY_WARMUP = 250


class Timeframe(Enum):
    M15 = 900
    H1 = 3600


@dataclass(frozen=True)
class Candle:
    """Internal OHLC record; timestamp is UTC Unix seconds at bar OPEN."""

    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    completed: bool


@dataclass(frozen=True)
class IndicatorPoint:
    """Value becomes available at closed_at, in UTC Unix seconds."""

    closed_at: int
    value: Decimal


class IndicatorError(ValueError):
    """Base exception for rejected data or unavailable indicator results."""


class InvalidCandleData(IndicatorError):
    """The caller supplied invalid, incomplete, future, or discontinuous data."""


class InsufficientHistory(IndicatorError):
    def __init__(self, required: int, available: int) -> None:
        self.required = required
        self.available = available
        super().__init__(f"Need {required} completed candles; received {available}")


class IndicatorArithmeticError(IndicatorError):
    """Values exceed the documented Decimal arithmetic context."""


def _context() -> Context:
    # Explicit settings prevent caller precision, rounding, and traps leaking in.
    return Context(
        prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN,
        Emin=-999999, Emax=999999, capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def validate_candles(
    candles: Sequence[Candle], *, timeframe: Timeframe, as_of: int
) -> tuple[Candle, ...]:
    """Snapshot and validate without sorting, filling gaps, or dropping rows.

    Only UTC-grid-aligned M15/H1 bars are supported. Each consecutive open time
    must differ by exactly the selected interval. Even completed=True cannot
    admit a bar whose close time is after as_of. Empty history is valid here;
    individual indicators raise InsufficientHistory for their own minima.
    """
    if not isinstance(timeframe, Timeframe):
        raise InvalidCandleData("timeframe must be Timeframe.M15 or Timeframe.H1")
    if type(as_of) is not int or as_of < 0:
        raise InvalidCandleData("as_of must be nonnegative UTC Unix seconds")
    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes, bytearray)):
        raise InvalidCandleData("candles must be a sequence of Candle records")
    result = tuple(candles)
    previous_timestamp = None
    interval = timeframe.value
    for candle in result:
        if not isinstance(candle, Candle):
            raise InvalidCandleData("Every observation must be a Candle")
        if type(candle.timestamp) is not int or candle.timestamp < 0:
            raise InvalidCandleData("Candle timestamp must be nonnegative UTC Unix seconds")
        if candle.timestamp % interval:
            raise InvalidCandleData("Candle open time is not aligned to the timeframe")
        if candle.completed is not True:
            raise InvalidCandleData("Only explicitly completed candles are accepted")
        if candle.timestamp + interval > as_of:
            raise InvalidCandleData("Candle has not closed at the requested as_of cutoff")
        if previous_timestamp is not None:
            delta = candle.timestamp - previous_timestamp
            if delta <= 0:
                raise InvalidCandleData("Duplicate or out-of-order candle timestamp")
            if delta != interval:
                raise InvalidCandleData("Candle history contains a timeframe continuity gap")
        for price in (candle.open, candle.high, candle.low, candle.close):
            if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
                raise InvalidCandleData("OHLC values must be positive finite Decimals")
        if not (candle.low <= candle.open <= candle.high
                and candle.low <= candle.close <= candle.high):
            raise InvalidCandleData("OHLC values are inconsistent")
        previous_timestamp = candle.timestamp
    return result


def _require_history(candles: tuple[Candle, ...], required: int) -> None:
    if len(candles) < required:
        raise InsufficientHistory(required, len(candles))


def _ranges(candles: tuple[Candle, ...]) -> tuple[Decimal, ...]:
    # Caller holds the fixed Decimal context. First bar uses high-low only.
    result = []
    previous_close = None
    for candle in candles:
        value = candle.high - candle.low
        if previous_close is not None:
            value = max(value, abs(candle.high - previous_close),
                        abs(candle.low - previous_close))
        result.append(value)
        previous_close = candle.close
    return tuple(result)


def ema(
    candles: Sequence[Candle], period: int, *, timeframe: Timeframe, as_of: int
) -> tuple[IndicatorPoint, ...]:
    """SMA-seeded EMA of closes, with the first point at the Nth bar close.

    alpha = 2/(N+1); next = previous + alpha*(close-previous). Raises
    InsufficientHistory instead of returning padded or partial seed values.
    """
    if type(period) is not int or period <= 0:
        raise InvalidCandleData("EMA period must be a positive integer")
    data = validate_candles(candles, timeframe=timeframe, as_of=as_of)
    _require_history(data, period)
    try:
        with localcontext(_context()):
            average = sum((bar.close for bar in data[:period]), Decimal(0)) / Decimal(period)
            alpha = Decimal(2) / Decimal(period + 1)
            result = [IndicatorPoint(data[period - 1].timestamp + timeframe.value, average)]
            for candle in data[period:]:
                average = average + alpha * (candle.close - average)
                result.append(IndicatorPoint(candle.timestamp + timeframe.value, average))
            return tuple(result)
    except DecimalException as exc:
        raise IndicatorArithmeticError("EMA exceeds the Decimal context") from exc


def true_ranges(
    candles: Sequence[Candle], *, timeframe: Timeframe, as_of: int
) -> tuple[IndicatorPoint, ...]:
    """TR=max(high-low, abs(high-prev_close), abs(low-prev_close)).

    The first supplied bar has no prior close; its TR is high-low. Price gaps
    count in TR, but missing time bars are rejected by continuity validation.
    """
    data = validate_candles(candles, timeframe=timeframe, as_of=as_of)
    _require_history(data, 1)
    try:
        with localcontext(_context()):
            values = _ranges(data)
            return tuple(IndicatorPoint(bar.timestamp + timeframe.value, value)
                         for bar, value in zip(data, values))
    except DecimalException as exc:
        raise IndicatorArithmeticError("True Range exceeds the Decimal context") from exc


def atr(
    candles: Sequence[Candle], *, timeframe: Timeframe, as_of: int
) -> tuple[IndicatorPoint, ...]:
    """Wilder ATR14: seed=mean(first 14 TRs); next=(previous*13+TR)/14.

    First-bar TR=high-low, so the first ATR point is at the 14th candle close.
    No zero padding, resampling, or missing-observation substitution occurs.
    """
    data = validate_candles(candles, timeframe=timeframe, as_of=as_of)
    _require_history(data, ATR_PERIOD)
    try:
        with localcontext(_context()):
            values = _ranges(data)
            average = sum(values[:ATR_PERIOD], Decimal(0)) / Decimal(ATR_PERIOD)
            result = [IndicatorPoint(data[ATR_PERIOD - 1].timestamp + timeframe.value, average)]
            for candle, value in zip(data[ATR_PERIOD:], values[ATR_PERIOD:]):
                average = (average * Decimal(13) + value) / Decimal(ATR_PERIOD)
                result.append(IndicatorPoint(candle.timestamp + timeframe.value, average))
            return tuple(result)
    except DecimalException as exc:
        raise IndicatorArithmeticError("ATR exceeds the Decimal context") from exc


def validate_tp1_warmup(
    candles: Sequence[Candle], *, as_of: int
) -> tuple[Candle, ...]:
    """Require 250 valid completed H1 bars, separately from indicator minima.

    Passing this history check does not establish a trading signal or entry.
    """
    data = validate_candles(candles, timeframe=Timeframe.H1, as_of=as_of)
    _require_history(data, TP1_HOURLY_WARMUP)
    return data
