"""Run with python -B -m wallex.market.frame_example; synthetic offline data only."""

import json
from pathlib import Path

from .datasets import load_dataset
from .frames import MarketFrameProposal, build_market_frame
from .normalization import Timeframe, TimestampUnit


def build_synthetic_frame() -> MarketFrameProposal:
    """Load package-relative fixtures; no dependency on the working directory."""
    fixtures = Path(__file__).resolve().parent / "fixtures"
    settings = dict(symbol="SYNTHETIC-BASE-QUOTE",
                    timestamp_unit=TimestampUnit.SECONDS, as_of=903600)
    m15 = load_dataset(fixtures / "synthetic_frame_m15.jsonl",
                       timeframe=Timeframe.M15, **settings)
    h1 = load_dataset(fixtures / "synthetic_frame_h1.jsonl",
                      timeframe=Timeframe.H1, **settings)
    return build_market_frame(m15, h1, base_asset="BASE", quote_asset="QUOTE",
                              as_of=settings["as_of"])


def main() -> None:
    frame = build_synthetic_frame()
    # A human-readable summary, NOT a canonical MarketFrame serializer/hash.
    print(json.dumps({
        "evidence_kind": "synthetic_fixture",
        "schema_version": frame.schema_version,
        "symbol": frame.symbol,
        "base_asset": frame.base_asset,
        "quote_asset": frame.quote_asset,
        "as_of": frame.as_of,
        "candles_15m_count": len(frame.candles_15m),
        "candles_1h_count": len(frame.candles_1h),
        "latest_close_15m": frame.candles_15m[-1].timestamp + Timeframe.M15.value,
        "latest_close_1h": frame.candles_1h[-1].timestamp + Timeframe.H1.value,
        "dataset_sha256_15m": frame.dataset_sha256_15m,
        "dataset_sha256_1h": frame.dataset_sha256_1h,
        "event_time": frame.event_time,
        "received_time": frame.received_time,
        "best_bid": frame.best_bid,
        "best_ask": frame.best_ask,
        "spread_pct": frame.spread_pct,
        "depth_valid": frame.depth_valid,
        "snapshot_hash": frame.snapshot_hash,
        "data_quality": frame.data_quality.value,
        "quality_reasons": [reason.value for reason in frame.quality_reasons],
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
