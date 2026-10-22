"""Provisional offline history assembly; no shared schema or strategy execution."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import re

from .datasets import LoadedDataset
from .normalization import (
    ErrorCode, MarketCandle, MarketDataError, Timeframe, TimestampUnit,
    normalize_candles,
)

FRAME_SCHEMA_VERSION = "market-frame-proposal-v0"
MIN_M15_HISTORY = 21
MIN_H1_HISTORY = 250


class FrameErrorCode(str, Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_DATASET = "INVALID_DATASET"
    INVALID_DATASET_HASH = "INVALID_DATASET_HASH"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    AS_OF_MISMATCH = "AS_OF_MISMATCH"
    CANDLE_METADATA_MISMATCH = "CANDLE_METADATA_MISMATCH"
    INVALID_NORMALIZED_CANDLE = "INVALID_NORMALIZED_CANDLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_HISTORY = "STALE_HISTORY"


class MarketFrameError(ValueError):
    """Failure with dataset name and, when applicable, candle index and field."""

    def __init__(
        self, code: FrameErrorCode | ErrorCode, message: str, *,
        dataset: str | None = None, index: int | None = None,
        field: str | None = None,
    ) -> None:
        self.code = code
        self.dataset = dataset
        self.index = index
        self.field = field
        location = dataset or "frame"
        if index is not None:
            location += f".candles[{index}]"
        if field is not None:
            location += f".{field}"
        super().__init__(f"{code.value} at {location}: {message}")


class FrameQuality(str, Enum):
    INCOMPLETE = "INCOMPLETE"


class FrameQualityReason(str, Enum):
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    DEPTH_UNAVAILABLE = "DEPTH_UNAVAILABLE"
    FRAME_TIME_SEMANTICS_PENDING = "FRAME_TIME_SEMANTICS_PENDING"
    SNAPSHOT_HASH_POLICY_PENDING = "SNAPSHOT_HASH_POLICY_PENDING"
    SHARED_SCHEMA_PENDING = "SHARED_SCHEMA_PENDING"


@dataclass(frozen=True)
class MarketFrameProposal:
    """Immutable internal container; use build_market_frame for validation.

    History validation does not establish full market readiness or authorization.
    Missing/pending fields cannot be supplied through this proposal constructor.
    Dataset hashes are producer claims, not a canonical hash of this frame.
    """

    symbol: str
    base_asset: str
    quote_asset: str
    as_of: int
    candles_15m: tuple[MarketCandle, ...]
    candles_1h: tuple[MarketCandle, ...]
    dataset_sha256_15m: str
    dataset_sha256_1h: str
    schema_version: str = field(default=FRAME_SCHEMA_VERSION, init=False)
    event_time: None = field(default=None, init=False)
    received_time: None = field(default=None, init=False)
    best_bid: None = field(default=None, init=False)
    best_ask: None = field(default=None, init=False)
    spread_pct: None = field(default=None, init=False)
    depth_valid: bool = field(default=False, init=False)
    snapshot_hash: None = field(default=None, init=False)
    data_quality: FrameQuality = field(default=FrameQuality.INCOMPLETE, init=False)
    quality_reasons: tuple[FrameQualityReason, ...] = field(default=(
        FrameQualityReason.QUOTE_UNAVAILABLE,
        FrameQualityReason.DEPTH_UNAVAILABLE,
        FrameQualityReason.FRAME_TIME_SEMANTICS_PENDING,
        FrameQualityReason.SNAPSHOT_HASH_POLICY_PENDING,
        FrameQualityReason.SHARED_SCHEMA_PENDING,
    ), init=False)


def _asset(value: object, name: str) -> None:
    if (not isinstance(value, str) or not value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise MarketFrameError(
            FrameErrorCode.INVALID_CONFIG, "explicit nonblank asset identifier required",
            field=name,
        )


def _history(
    dataset: LoadedDataset, *, timeframe: Timeframe, minimum: int,
    as_of: int, name: str,
) -> tuple[MarketCandle, ...]:
    if not isinstance(dataset, LoadedDataset):
        raise MarketFrameError(
            FrameErrorCode.INVALID_DATASET, "expected LoadedDataset", dataset=name,
        )
    if dataset.timeframe is not timeframe:
        raise MarketFrameError(
            FrameErrorCode.TIMEFRAME_MISMATCH, f"expected {timeframe.name}",
            dataset=name, field="timeframe",
        )
    if type(dataset.as_of) is not int or dataset.as_of != as_of:
        raise MarketFrameError(
            FrameErrorCode.AS_OF_MISMATCH, "dataset cutoff must equal frame as_of",
            dataset=name, field="as_of",
        )
    if not isinstance(dataset.timestamp_unit, TimestampUnit):
        raise MarketFrameError(
            FrameErrorCode.INVALID_DATASET, "explicit source TimestampUnit required",
            dataset=name, field="timestamp_unit",
        )
    if not isinstance(dataset.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", dataset.sha256) is None:
        raise MarketFrameError(
            FrameErrorCode.INVALID_DATASET_HASH, "expected lowercase hexadecimal SHA-256",
            dataset=name, field="sha256",
        )
    if not isinstance(dataset.candles, tuple):
        raise MarketFrameError(
            FrameErrorCode.INVALID_DATASET, "normalized candles must be a tuple",
            dataset=name, field="candles",
        )

    records = []
    for index, candle in enumerate(dataset.candles):
        if not isinstance(candle, MarketCandle):
            raise MarketFrameError(
                FrameErrorCode.INVALID_NORMALIZED_CANDLE, "expected MarketCandle",
                dataset=name, index=index,
            )
        for key, expected in (("symbol", dataset.symbol), ("timeframe", timeframe)):
            actual = getattr(candle, key)
            if actual != expected or (key == "timeframe" and actual is not expected):
                raise MarketFrameError(
                    FrameErrorCode.CANDLE_METADATA_MISMATCH, "candle identity disagrees with dataset",
                    dataset=name, index=index, field=key,
                )
        # LoadedDataset is already normalized. A hand-constructed record must
        # not smuggle raw text/integers through a second numeric coercion.
        for key in ("open", "high", "low", "close", "volume"):
            if not isinstance(getattr(candle, key), Decimal):
                raise MarketFrameError(
                    FrameErrorCode.INVALID_NORMALIZED_CANDLE, "normalized numbers must be Decimal",
                    dataset=name, index=index, field=key,
                )
        records.append({key: getattr(candle, key) for key in (
            "timestamp", "open", "high", "low", "close", "volume", "completed",
        )})
    try:
        # Input timestamps here are ALWAYS normalized seconds, even when the
        # original file was loaded with TimestampUnit.MILLISECONDS.
        candles = normalize_candles(
            records, symbol=dataset.symbol, timeframe=timeframe,
            timestamp_unit=TimestampUnit.SECONDS, as_of=as_of,
        )
    except MarketDataError as exc:
        raise MarketFrameError(
            exc.code, str(exc), dataset=name, index=exc.index, field=exc.field,
        ) from exc
    if len(candles) < minimum:
        raise MarketFrameError(
            FrameErrorCode.INSUFFICIENT_HISTORY,
            f"need at least {minimum} candles; received {len(candles)}", dataset=name,
        )
    latest_close = candles[-1].timestamp + timeframe.value
    expected_close = (as_of // timeframe.value) * timeframe.value
    if latest_close != expected_close:
        raise MarketFrameError(
            FrameErrorCode.STALE_HISTORY,
            f"latest close {latest_close} must equal {expected_close} at as_of={as_of}",
            dataset=name, index=len(candles) - 1, field="timestamp",
        )
    return candles


def build_market_frame(
    candles_15m_dataset: LoadedDataset, candles_1h_dataset: LoadedDataset, *,
    base_asset: str, quote_asset: str, as_of: int,
) -> MarketFrameProposal:
    """Validate both histories and assemble an explicitly incomplete proposal.

    Both datasets must share symbol/cutoff; M15 needs 21 bars and H1 250.
    Latest closes must equal floor(as_of/interval)*interval. No time, quote,
    book, spread, canonical hash, indicator or permission is inferred.
    """
    if type(as_of) is not int or as_of < 0:
        raise MarketFrameError(
            FrameErrorCode.INVALID_CONFIG, "as_of must be nonnegative integer UTC Unix seconds",
            field="as_of",
        )
    _asset(base_asset, "base_asset")
    _asset(quote_asset, "quote_asset")
    if base_asset == quote_asset:
        raise MarketFrameError(
            FrameErrorCode.INVALID_CONFIG, "base and quote assets must be different",
            field="quote_asset",
        )
    m15 = _history(candles_15m_dataset, timeframe=Timeframe.M15, minimum=MIN_M15_HISTORY,
                   as_of=as_of, name="candles_15m_dataset")
    h1 = _history(candles_1h_dataset, timeframe=Timeframe.H1, minimum=MIN_H1_HISTORY,
                  as_of=as_of, name="candles_1h_dataset")
    if candles_15m_dataset.symbol != candles_1h_dataset.symbol:
        raise MarketFrameError(
            FrameErrorCode.SYMBOL_MISMATCH, "both histories must have the same exact symbol",
            field="symbol",
        )
    return MarketFrameProposal(
        symbol=candles_15m_dataset.symbol, base_asset=base_asset, quote_asset=quote_asset,
        as_of=as_of, candles_15m=m15, candles_1h=h1,
        dataset_sha256_15m=candles_15m_dataset.sha256,
        dataset_sha256_1h=candles_1h_dataset.sha256,
    )
