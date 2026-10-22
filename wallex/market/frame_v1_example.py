"""Run python -B -m wallex.market.frame_v1_example. Entirely SYNTHETIC evidence.

    This invented scenario explicitly declares receipt at close+1 for its invented
    candles. That assignment is only fixture construction; no production loader
    or replay helper substitutes close time for unknown receipt evidence.
"""

from decimal import Decimal
import json
from pathlib import Path

from .datasets import load_dataset
from .frame_v1 import (
    AvailableCandle, BookLevelV1, DepthSnapshotV1, FramePolicyV1, QuoteSnapshotV1,
    build_market_frame_v1, verify_snapshot_hash,
)
from .normalization import Timeframe, TimestampUnit


def synthetic_inputs_v1() -> dict:
    """Explicit invented times, assets, quote/book and test-only policy values."""
    root = Path(__file__).resolve().parent / "fixtures"
    symbol, cutoff = "SYNTHETIC-BASE-QUOTE", 903604
    histories = []
    for name, timeframe in (("synthetic_frame_m15.jsonl", Timeframe.M15),
                             ("synthetic_frame_h1.jsonl", Timeframe.H1)):
        dataset = load_dataset(root / name, symbol=symbol, timeframe=timeframe,
                               timestamp_unit=TimestampUnit.SECONDS, as_of=cutoff)
        histories.append(tuple(AvailableCandle(
            candle, candle.timestamp + timeframe.value + 1, "synthetic-داده-" + timeframe.name,
        ) for candle in dataset.candles))
    identity = dict(symbol=symbol, base_asset="BASE", quote_asset="QUOTE")
    quote = QuoteSnapshotV1(**identity, source_id="synthetic-quote", event_time=903601,
                            received_time=903602, best_bid=Decimal("99.90"), best_ask=Decimal("100.10"))
    depth = DepthSnapshotV1(**identity, source_id="synthetic-depth", event_time=903602,
                            received_time=903603,
                            bids=(BookLevelV1(Decimal("99.9"), Decimal(2)),
                                  BookLevelV1(Decimal("99.8"), Decimal(3))),
                            asks=(BookLevelV1(Decimal("100.1"), Decimal(2)),
                                  BookLevelV1(Decimal("100.2"), Decimal(3))))
    return dict(candles_15m=histories[0], candles_1h=histories[1], **identity,
                as_of=cutoff, quote=quote, depth=depth,
                policy=FramePolicyV1(5, 5, Decimal("0.20")))


def build_synthetic_frame_v1():
    return build_market_frame_v1(**synthetic_inputs_v1())


def main() -> None:
    frame = build_synthetic_frame_v1()
    print(json.dumps({
        "evidence_kind": "synthetic_fixture", "schema_version": frame.schema_version,
        "contract_status": frame.contract_status, "as_of": frame.as_of,
        "event_time": frame.event_time, "received_time": frame.received_time,
        "candles_15m_count": len(frame.candles_15m), "candles_1h_count": len(frame.candles_1h),
        "spread_pct": str(frame.spread_pct), "depth_valid": frame.depth_valid,
        "quality": frame.quality.value, "quality_reasons": [r.value for r in frame.quality_reasons],
        "snapshot_hash": frame.snapshot_hash, "hash_verified": verify_snapshot_hash(frame),
        "executable_ready": frame.executable_ready,
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
