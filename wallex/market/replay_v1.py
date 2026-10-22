"""Explicit availability-aware selection; leaves ReplayPlan/v0 behavior unchanged."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .frame_v1 import (
    AvailableCandle, FrameV1Error, V1ErrorCode, _identifier, _timestamp, _validated_history,
)
from .frames import MIN_H1_HISTORY, MIN_M15_HISTORY
from .normalization import ErrorCode, MarketCandle, Timeframe


class AvailabilityReasonV1(str, Enum):
    OPEN_CANDLE = "OPEN_CANDLE"
    FUTURE_CANDLE = "FUTURE_CANDLE"
    MISSING_RECEIVED_TIME = "MISSING_RECEIVED_TIME"
    NOT_RECEIVED = "NOT_RECEIVED"


@dataclass(frozen=True)
class AvailabilityIssueV1:
    index: int
    timestamp: int
    reason: AvailabilityReasonV1


@dataclass(frozen=True)
class HistorySelectionV1:
    candles: tuple[AvailableCandle, ...]
    excluded: tuple[AvailabilityIssueV1, ...]
    latest_expected_available: bool
    minimum_history_met: bool

    @property
    def complete(self) -> bool:
        """History checks only, not Quote/Depth completeness or trading permission."""
        return self.latest_expected_available and self.minimum_history_met


def select_available_history_v1(
    observations: Sequence[AvailableCandle], *, symbol: str, timeframe: Timeframe, as_of: int,
) -> HistorySelectionV1:
    """Select closed AND received observations, reporting each exclusion explicitly.

    Input order/identity/timestamp types are checked before selection. Retained
    history uses the existing normalizer through v1's validation path. An internal
    missing available bar raises rather than filling/reordering. A missing latest
    bar returns complete=False; a caller cannot pass it as a valid fresh Frame.
    Missing receipt evidence is never replaced by close time. No grace/delay is
    inferred and arbitrary explicit decision seconds are supported.
    """
    _timestamp(as_of, "as_of")
    _identifier(symbol, "symbol")
    if not isinstance(timeframe, Timeframe):
        raise FrameV1Error(V1ErrorCode.INVALID_CONFIG, "explicit Timeframe required", field="timeframe")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes, bytearray)):
        raise FrameV1Error(V1ErrorCode.INVALID_HISTORY, "sequence of AvailableCandle required")
    available, excluded = [], []
    previous = None
    seen = set()
    for index, observation in enumerate(tuple(observations)):
        if not isinstance(observation, AvailableCandle) or not isinstance(observation.candle, MarketCandle):
            raise FrameV1Error(V1ErrorCode.INVALID_HISTORY, "AvailableCandle required", index=index)
        candle = observation.candle
        if candle.symbol != symbol or candle.timeframe is not timeframe:
            raise FrameV1Error(V1ErrorCode.HISTORY_IDENTITY_MISMATCH, "history identity mismatch", index=index)
        _identifier(observation.source_id, "source_id")
        _timestamp(candle.timestamp, "timestamp")
        if candle.timestamp in seen or (previous is not None and candle.timestamp < previous):
            code = ErrorCode.DUPLICATE_TIMESTAMP if candle.timestamp in seen else ErrorCode.OUT_OF_ORDER
            raise FrameV1Error(code, "selection input must retain chronological order", index=index)
        previous = candle.timestamp
        seen.add(candle.timestamp)
        if observation.received_time is not None:
            _timestamp(observation.received_time, "received_time")
        if type(candle.completed) is not bool:
            raise FrameV1Error(ErrorCode.INVALID_COMPLETION, "boolean completed required", index=index)
        close = candle.timestamp + timeframe.value
        if candle.completed and observation.received_time is not None and observation.received_time < close:
            raise FrameV1Error(V1ErrorCode.INVALID_TIME_ORDER, "completed bar received before close", index=index)
        if not candle.completed:
            reason = AvailabilityReasonV1.OPEN_CANDLE
        elif close > as_of:
            reason = AvailabilityReasonV1.FUTURE_CANDLE
        elif observation.received_time is None:
            reason = AvailabilityReasonV1.MISSING_RECEIVED_TIME
        elif observation.received_time > as_of:
            reason = AvailabilityReasonV1.NOT_RECEIVED
        else:
            available.append(observation)
            continue
        excluded.append(AvailabilityIssueV1(index, candle.timestamp, reason))
    candles = _validated_history(available, symbol=symbol, timeframe=timeframe, as_of=as_of)
    latest = bool(candles) and candles[-1].candle.timestamp + timeframe.value == as_of // timeframe.value * timeframe.value
    minimum = MIN_M15_HISTORY if timeframe is Timeframe.M15 else MIN_H1_HISTORY
    return HistorySelectionV1(candles, tuple(excluded), latest, len(candles) >= minimum)
