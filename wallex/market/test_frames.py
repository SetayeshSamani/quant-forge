"""Provisional-frame boundaries, forged model rejection, and synthetic fixtures."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
import unittest

from . import (
    FRAME_SCHEMA_VERSION, ErrorCode, FrameErrorCode, FrameQuality, FrameQualityReason,
    LoadedDataset, MarketDataError, MarketFrameError, Timeframe, TimestampUnit,
    build_market_frame, normalize_candles,
)
from .frame_example import build_synthetic_frame


AS_OF = 1800000
SYMBOL = "SYNTHETIC-INSTRUMENT"
_DEFAULT = object()


def dataset(timeframe, count, *, as_of=AS_OF, latest_close=None, symbol=SYMBOL,
            timestamp_unit=TimestampUnit.SECONDS):
    """Synthetic producer with a digest of its actual JSONL byte representation."""
    if latest_close is None:
        latest_close = as_of // timeframe.value * timeframe.value
    multiplier = 1000 if timestamp_unit is TimestampUnit.MILLISECONDS else 1
    records = [dict(timestamp=(latest_close - (count - i) * timeframe.value) * multiplier,
                    open="100", high="101", low="99", close="100", volume="0",
                    completed=True) for i in range(count)]
    payload = "\n".join(json.dumps(r, separators=(",", ":")) for r in records).encode("utf-8")
    candles = normalize_candles(records, symbol=symbol, timeframe=timeframe,
                                timestamp_unit=timestamp_unit, as_of=as_of)
    return LoadedDataset(candles=candles, sha256=sha256(payload).hexdigest(), symbol=symbol,
                         timeframe=timeframe, timestamp_unit=timestamp_unit, as_of=as_of)


def change_candle(source, index, **changes):
    candles = list(source.candles)
    candles[index] = replace(candles[index], **changes)
    return replace(source, candles=tuple(candles))


class MarketFrameTests(unittest.TestCase):
    def setUp(self):
        self.m15 = dataset(Timeframe.M15, 21)
        self.h1 = dataset(Timeframe.H1, 250)

    def build(self, m15=_DEFAULT, h1=_DEFAULT, **changes):
        settings = dict(base_asset="EXPLICIT-BASE", quote_asset="EXPLICIT-QUOTE", as_of=AS_OF)
        return build_market_frame(
            self.m15 if m15 is _DEFAULT else m15,
            self.h1 if h1 is _DEFAULT else h1,
            **(settings | changes),
        )

    def assert_failure(self, code, *, source=None, index=None, field=None, **inputs):
        with self.assertRaises(MarketFrameError) as caught:
            self.build(**inputs)
        error = caught.exception
        self.assertIs(error.code, code)
        self.assertEqual((error.dataset, error.index, error.field), (source, index, field))
        return error

    def test_assembles_valid_histories_and_preserves_provenance_and_assets(self):
        frame = self.build()
        self.assertEqual(frame.schema_version, FRAME_SCHEMA_VERSION)
        self.assertEqual(frame.schema_version, "market-frame-proposal-v0")
        self.assertEqual((frame.symbol, frame.base_asset, frame.quote_asset, frame.as_of),
                         (SYMBOL, "EXPLICIT-BASE", "EXPLICIT-QUOTE", AS_OF))
        self.assertEqual((frame.candles_15m, frame.candles_1h),
                         (self.m15.candles, self.h1.candles))
        self.assertEqual((frame.dataset_sha256_15m, frame.dataset_sha256_1h),
                         (self.m15.sha256, self.h1.sha256))
        self.assertNotEqual(frame.dataset_sha256_15m, frame.dataset_sha256_1h)

    def test_missing_fields_stay_unknown_and_quality_explicitly_incomplete(self):
        frame = self.build()
        for field in ("event_time", "received_time", "best_bid", "best_ask",
                      "spread_pct", "snapshot_hash"):
            with self.subTest(field=field):
                self.assertIsNone(getattr(frame, field))
        self.assertIs(frame.depth_valid, False)
        self.assertIs(frame.data_quality, FrameQuality.INCOMPLETE)
        self.assertEqual(frame.quality_reasons, (
            FrameQualityReason.QUOTE_UNAVAILABLE,
            FrameQualityReason.DEPTH_UNAVAILABLE,
            FrameQualityReason.FRAME_TIME_SEMANTICS_PENDING,
            FrameQualityReason.SNAPSHOT_HASH_POLICY_PENDING,
            FrameQualityReason.SHARED_SCHEMA_PENDING,
        ))

    def test_inputs_unchanged_results_immutable_and_repeatable(self):
        before = deepcopy((self.m15, self.h1))
        frame = self.build()
        self.assertEqual((self.m15, self.h1), before)
        self.assertEqual(frame, self.build())
        for value in (frame.candles_15m, frame.candles_1h, frame.quality_reasons):
            self.assertIsInstance(value, tuple)
        with self.assertRaises(FrozenInstanceError):
            frame.best_bid = Decimal(100)
        with self.assertRaises(FrozenInstanceError):
            frame.candles_1h[0].close = Decimal(200)
        with self.assertRaises(TypeError):
            replace(frame, depth_valid=True)

    def test_configuration_is_explicit_and_not_inferred_from_symbol(self):
        frame = self.build(base_asset="BaseCase", quote_asset="QuoteCase")
        self.assertEqual((frame.base_asset, frame.quote_asset), ("BaseCase", "QuoteCase"))
        options = dict(base_asset="BASE", quote_asset="QUOTE", as_of=AS_OF)
        for field in options:
            with self.subTest(field=field), self.assertRaises(TypeError):
                build_market_frame(self.m15, self.h1,
                                   **{k: v for k, v in options.items() if k != field})

    def test_invalid_cutoffs_and_assets_are_rejected(self):
        for cutoff in (None, True, -1, "1800000", 1800000.0, Decimal(AS_OF)):
            with self.subTest(cutoff=cutoff):
                self.assert_failure(FrameErrorCode.INVALID_CONFIG, field="as_of", as_of=cutoff)
        for field in ("base_asset", "quote_asset"):
            for value in (None, "", " ", "A B", "A\n", "A\x00", 1):
                with self.subTest(field=field, value=value):
                    self.assert_failure(FrameErrorCode.INVALID_CONFIG, field=field,
                                        **{field: value})
        self.assert_failure(FrameErrorCode.INVALID_CONFIG, field="quote_asset",
                            base_asset="SAME", quote_asset="SAME")

    def test_both_slots_require_loaded_datasets(self):
        for slot, name in (("m15", "candles_15m_dataset"), ("h1", "candles_1h_dataset")):
            for value in (None, {}, [], "dataset"):
                with self.subTest(slot=slot, value=value):
                    self.assert_failure(FrameErrorCode.INVALID_DATASET, source=name,
                                        **{slot: value})

    def test_both_histories_must_have_same_exact_symbol(self):
        self.assert_failure(FrameErrorCode.SYMBOL_MISMATCH, field="symbol",
                            h1=dataset(Timeframe.H1, 250, symbol="OTHER-INSTRUMENT"))

    def test_expected_timeframes_are_enforced(self):
        self.assert_failure(FrameErrorCode.TIMEFRAME_MISMATCH,
                            source="candles_15m_dataset", field="timeframe", m15=self.h1)
        self.assert_failure(FrameErrorCode.TIMEFRAME_MISMATCH,
                            source="candles_1h_dataset", field="timeframe", h1=self.m15)
        for value in ("M15", 900, None):
            with self.subTest(value=value):
                self.assert_failure(FrameErrorCode.TIMEFRAME_MISMATCH,
                                    source="candles_15m_dataset", field="timeframe",
                                    m15=replace(self.m15, timeframe=value))

    def test_cutoff_metadata_must_equal_frame_as_of_without_type_coercion(self):
        for slot, data, name in (("m15", self.m15, "candles_15m_dataset"),
                                 ("h1", self.h1, "candles_1h_dataset")):
            for cutoff in (AS_OF - 1, True, float(AS_OF), str(AS_OF), None):
                with self.subTest(slot=slot, cutoff=cutoff):
                    self.assert_failure(FrameErrorCode.AS_OF_MISMATCH, source=name,
                                        field="as_of", **{slot: replace(data, as_of=cutoff)})

    def test_source_unit_metadata_must_be_explicit(self):
        self.assert_failure(FrameErrorCode.INVALID_DATASET, source="candles_1h_dataset",
                            field="timestamp_unit", h1=replace(self.h1, timestamp_unit="seconds"))

    def test_millisecond_source_timestamps_are_not_converted_twice(self):
        m15 = dataset(Timeframe.M15, 21, timestamp_unit=TimestampUnit.MILLISECONDS)
        h1 = dataset(Timeframe.H1, 250, timestamp_unit=TimestampUnit.MILLISECONDS)
        frame = self.build(m15=m15, h1=h1)
        self.assertEqual(frame.candles_15m, self.m15.candles)
        self.assertEqual(frame.candles_1h, self.h1.candles)
        self.assertEqual(frame.candles_1h[-1].timestamp, AS_OF - 3600)
        self.assertEqual((frame.dataset_sha256_15m, frame.dataset_sha256_1h),
                         (m15.sha256, h1.sha256))

    def test_dataset_hashes_are_checked_as_claims_not_recomputed_from_candles(self):
        alternate = "a" * 64
        self.assertEqual(self.build(m15=replace(self.m15, sha256=alternate)).dataset_sha256_15m,
                         alternate)
        for bad in (None, 123, "", "f" * 63, "F" * 64, "g" * 64):
            with self.subTest(bad=bad):
                self.assert_failure(FrameErrorCode.INVALID_DATASET_HASH,
                                    source="candles_15m_dataset", field="sha256",
                                    m15=replace(self.m15, sha256=bad))

    def test_mutable_or_malformed_candle_containers_are_rejected(self):
        for bad in (None, list(self.h1.candles), "candles"):
            with self.subTest(bad=type(bad).__name__):
                self.assert_failure(FrameErrorCode.INVALID_DATASET, source="candles_1h_dataset",
                                    field="candles", h1=replace(self.h1, candles=bad))
        for bad in (None, {}, "candle"):
            with self.subTest(bad=bad):
                self.assert_failure(FrameErrorCode.INVALID_NORMALIZED_CANDLE,
                                    source="candles_1h_dataset", index=1,
                                    h1=replace(self.h1, candles=(self.h1.candles[0], bad)))

    def test_per_candle_symbol_and_timeframe_are_checked_in_both_histories(self):
        for slot, data, name in (("m15", self.m15, "candles_15m_dataset"),
                                 ("h1", self.h1, "candles_1h_dataset")):
            for changes in ({"symbol": "OTHER"}, {"timeframe": "H1"}, {"timeframe": 900}):
                with self.subTest(slot=slot, changes=changes):
                    field = next(iter(changes))
                    self.assert_failure(FrameErrorCode.CANDLE_METADATA_MISMATCH,
                                        source=name, index=2, field=field,
                                        **{slot: change_candle(data, 2, **changes)})

    def test_direct_model_numbers_must_already_be_decimal(self):
        for field in ("open", "high", "low", "close", "volume"):
            for bad in ("100", 100, 100.0, True, None):
                with self.subTest(field=field, bad=bad):
                    self.assert_failure(FrameErrorCode.INVALID_NORMALIZED_CANDLE,
                                        source="candles_15m_dataset", index=2, field=field,
                                        m15=change_candle(self.m15, 2, **{field: bad}))

    def test_forged_numeric_domains_use_normalizer_and_preserve_cause(self):
        for changes, code, field in (
            ({"open": Decimal("NaN")}, ErrorCode.INVALID_NUMBER, "open"),
            ({"volume": Decimal("Infinity")}, ErrorCode.INVALID_NUMBER, "volume"),
            ({"close": Decimal(0)}, ErrorCode.NON_POSITIVE_PRICE, "close"),
            ({"volume": Decimal(-1)}, ErrorCode.NEGATIVE_VOLUME, "volume"),
            ({"high": Decimal(98)}, ErrorCode.INVALID_OHLC, "ohlc"),
        ):
            with self.subTest(changes=changes):
                error = self.assert_failure(code, source="candles_15m_dataset", index=2,
                                            field=field, m15=change_candle(self.m15, 2, **changes))
                self.assertIsInstance(error.__cause__, MarketDataError)
                self.assertEqual((error.code, error.index, error.field),
                                 (error.__cause__.code, error.__cause__.index, error.__cause__.field))

    def test_open_future_and_bad_timestamp_candles_are_rejected_in_both_histories(self):
        for slot, data, name in (("m15", self.m15, "candles_15m_dataset"),
                                 ("h1", self.h1, "candles_1h_dataset")):
            cases = (({"completed": False}, ErrorCode.INCOMPLETE_CANDLE, "completed"),
                     ({"completed": 1}, ErrorCode.INVALID_COMPLETION, "completed"),
                     ({"timestamp": AS_OF}, ErrorCode.FUTURE_CANDLE, "timestamp"),
                     ({"timestamp": True}, ErrorCode.INVALID_TIMESTAMP, "timestamp"),
                     ({"timestamp": 1}, ErrorCode.UNALIGNED_TIMESTAMP, "timestamp"))
            for changes, code, field in cases:
                with self.subTest(slot=slot, changes=changes):
                    self.assert_failure(code, source=name, index=0, field=field,
                                        **{slot: change_candle(data, 0, **changes)})

    def test_duplicate_unsorted_and_missing_rows_are_rejected_before_warmup(self):
        for slot, data, name in (("m15", self.m15, "candles_15m_dataset"),
                                 ("h1", self.h1, "candles_1h_dataset")):
            original = data.candles
            cases = (
                ((original[0], original[0]) + original[2:], ErrorCode.DUPLICATE_TIMESTAMP, 1),
                ((original[1], original[0]) + original[2:], ErrorCode.OUT_OF_ORDER, 1),
                (original[:1] + original[2:], ErrorCode.MISSING_CANDLE, 1),
            )
            for candles, code, index in cases:
                with self.subTest(slot=slot, code=code):
                    self.assert_failure(code, source=name, index=index, field="timestamp",
                                        **{slot: replace(data, candles=candles)})
            self.assertEqual(data.candles, original)

    def test_m15_history_boundary_and_extra_history(self):
        for count in (0, 20):
            with self.subTest(count=count):
                error = self.assert_failure(FrameErrorCode.INSUFFICIENT_HISTORY,
                                            source="candles_15m_dataset",
                                            m15=dataset(Timeframe.M15, count))
                self.assertIn(f"need at least 21 candles; received {count}", str(error))
        for count in (21, 22, 100):
            with self.subTest(count=count):
                self.assertEqual(len(self.build(m15=dataset(Timeframe.M15, count)).candles_15m), count)

    def test_h1_history_boundary_and_extra_history(self):
        for count in (0, 249):
            with self.subTest(count=count):
                error = self.assert_failure(FrameErrorCode.INSUFFICIENT_HISTORY,
                                            source="candles_1h_dataset",
                                            h1=dataset(Timeframe.H1, count))
                self.assertIn(f"need at least 250 candles; received {count}", str(error))
        for count in (250, 251):
            with self.subTest(count=count):
                self.assertEqual(len(self.build(h1=dataset(Timeframe.H1, count)).candles_1h), count)

    def test_freshness_at_and_between_timeframe_boundaries(self):
        for offset, m15_close, h1_close in (
            (0, AS_OF, AS_OF),
            (1, AS_OF, AS_OF),
            (899, AS_OF, AS_OF),
            (900, AS_OF + 900, AS_OF),
            (3599, AS_OF + 2700, AS_OF),
            (3600, AS_OF + 3600, AS_OF + 3600),
        ):
            with self.subTest(offset=offset):
                cutoff = AS_OF + offset
                frame = self.build(m15=dataset(Timeframe.M15, 21, as_of=cutoff),
                                   h1=dataset(Timeframe.H1, 250, as_of=cutoff), as_of=cutoff)
                self.assertEqual(frame.candles_15m[-1].timestamp + 900, m15_close)
                self.assertEqual(frame.candles_1h[-1].timestamp + 3600, h1_close)

    def test_contiguous_but_old_history_is_rejected(self):
        for slot, timeframe, count, name in (
            ("m15", Timeframe.M15, 21, "candles_15m_dataset"),
            ("h1", Timeframe.H1, 250, "candles_1h_dataset"),
        ):
            with self.subTest(slot=slot):
                old = dataset(timeframe, count, latest_close=AS_OF - timeframe.value)
                self.assert_failure(FrameErrorCode.STALE_HISTORY, source=name,
                                    index=count - 1, field="timestamp", **{slot: old})

    def test_new_quarter_hour_requires_new_m15_but_uses_previous_complete_hour(self):
        cutoff = AS_OF + 900
        h1 = dataset(Timeframe.H1, 250, as_of=cutoff, latest_close=AS_OF)
        old_m15 = dataset(Timeframe.M15, 21, as_of=cutoff, latest_close=AS_OF)
        self.assert_failure(FrameErrorCode.STALE_HISTORY, source="candles_15m_dataset",
                            index=20, field="timestamp", m15=old_m15, h1=h1, as_of=cutoff)
        fresh_m15 = dataset(Timeframe.M15, 21, as_of=cutoff)
        self.assertEqual(self.build(m15=fresh_m15, h1=h1, as_of=cutoff).as_of, cutoff)

    def test_new_hour_requires_new_h1(self):
        cutoff = AS_OF + 3600
        m15 = dataset(Timeframe.M15, 21, as_of=cutoff)
        old_h1 = dataset(Timeframe.H1, 250, as_of=cutoff, latest_close=AS_OF)
        self.assert_failure(FrameErrorCode.STALE_HISTORY, source="candles_1h_dataset",
                            index=249, field="timestamp", m15=m15, h1=old_h1, as_of=cutoff)


class SyntheticFrameExampleTests(unittest.TestCase):
    def test_loads_both_fixtures_with_correct_counts_cutoff_and_byte_hashes(self):
        frame = build_synthetic_frame()
        self.assertEqual((len(frame.candles_15m), len(frame.candles_1h)), (21, 250))
        self.assertEqual(frame.symbol, "SYNTHETIC-BASE-QUOTE")
        self.assertEqual(frame.as_of, 903600)
        self.assertEqual(frame.candles_15m[-1].timestamp + 900, 903600)
        self.assertEqual(frame.candles_1h[-1].timestamp + 3600, 903600)
        fixtures = Path(__file__).resolve().parent / "fixtures"
        self.assertEqual(frame.dataset_sha256_15m,
                         sha256((fixtures / "synthetic_frame_m15.jsonl").read_bytes()).hexdigest())
        self.assertEqual(frame.dataset_sha256_1h,
                         sha256((fixtures / "synthetic_frame_h1.jsonl").read_bytes()).hexdigest())
        self.assertIs(frame.data_quality, FrameQuality.INCOMPLETE)

    def test_example_runs_from_unrelated_directory_without_strategy_or_execution_imports(self):
        root = Path(__file__).resolve().parents[2]
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from wallex.market.frame_example import main
main()
assert not any(name.startswith(('wallex.strategy', 'wallex.execution', 'wallex.risk'))
               for name in sys.modules)
"""
        with TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-c", script, str(root)],
                                    cwd=directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["evidence_kind"], "synthetic_fixture")
        self.assertEqual((summary["candles_15m_count"], summary["candles_1h_count"]), (21, 250))
        self.assertEqual(summary["data_quality"], "INCOMPLETE")
        self.assertIsNone(summary["snapshot_hash"])
        self.assertFalse(summary["depth_valid"])


if __name__ == "__main__":
    unittest.main()
