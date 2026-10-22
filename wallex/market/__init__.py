"""Roham-owned provisional normalization and local datasets; no exchange access."""

from .normalization import (
    ErrorCode, MarketCandle, MarketDataError, Timeframe, TimestampUnit,
    normalize_candles,
)
from .datasets import DatasetError, DatasetErrorCode, LoadedDataset, load_dataset
from .frames import (
    FRAME_SCHEMA_VERSION, MIN_H1_HISTORY, MIN_M15_HISTORY, FrameErrorCode,
    FrameQuality, FrameQualityReason, MarketFrameError, MarketFrameProposal,
    build_market_frame,
)

__all__ = [
    "ErrorCode", "MarketCandle", "MarketDataError", "Timeframe", "TimestampUnit",
    "normalize_candles",
    "DatasetError", "DatasetErrorCode", "LoadedDataset", "load_dataset",
    "FRAME_SCHEMA_VERSION", "MIN_H1_HISTORY", "MIN_M15_HISTORY", "FrameErrorCode",
    "FrameQuality", "FrameQualityReason", "MarketFrameError", "MarketFrameProposal",
    "build_market_frame",
]
