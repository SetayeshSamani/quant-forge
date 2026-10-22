"""Draft v1 tests use invented availability evidence, never real feed timestamps."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, InvalidOperation, ROUND_UP, localcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
import unittest

from wallex.market.frame_v1 import (
    AvailableCandle, BookLevelV1, FramePolicyV1, FrameV1Error, QualityReasonV1,
    QualityV1, V1ErrorCode, build_market_frame_v1, canonical_decimal,
    canonical_frame_json, compute_snapshot_hash, verify_snapshot_hash,
)
from wallex.market.frame_v1_example import build_synthetic_frame_v1, synthetic_inputs_v1
from wallex.market.normalization import ErrorCode, MarketDataError, Timeframe

D = Decimal
EXPECTED_HASH = "9ec067694d06ec1c428f4cf7a75df36880a076760ae764c5d0e734d31afa6ffc"


def changed_bar(history, index=0, **changes):
    result = list(history)
    result[index] = replace(result[index], candle=replace(result[index].candle, **changes))
    return tuple(result)


class FrameV1Tests(unittest.TestCase):
    def setUp(self):
        self.inputs = synthetic_inputs_v1()

    def build(self, **changes):
        return build_market_frame_v1(**(self.inputs | changes))

    def failure(self, code, **changes):
        with self.assertRaises(FrameV1Error) as caught:
            self.build(**changes)
        self.assertIs(caught.exception.code, code)
        return caught.exception

    def test_valid_frame_has_distinct_decision_event_and_receipt_times(self):
        frame = self.build()
        self.assertEqual((frame.as_of, frame.event_time, frame.received_time), (903604, 903600, 903603))
        self.assertEqual((len(frame.candles_15m), len(frame.candles_1h)), (21, 250))
        self.assertEqual(frame.schema_version, "marketframe-v1")
        self.assertEqual(frame.contract_status, "DRAFT")
        self.assertIs(frame.quality, QualityV1.COMPLETE)
        self.assertEqual(frame.quality_reasons, ())
        self.assertFalse(frame.executable_ready)
        self.assertTrue(frame.depth_valid)
        self.assertEqual(frame.spread_pct, D("0.20"))
        self.assertEqual(frame.source_ids, tuple(sorted({item.source_id for item in
                         (*frame.candles_15m, *frame.candles_1h, frame.quote, frame.depth)})))

    def test_inputs_preserved_and_output_deeply_immutable_and_repeatable(self):
        before = deepcopy(self.inputs)
        frame = self.build()
        self.assertEqual(self.inputs, before)
        self.assertEqual(frame, self.build())
        with self.assertRaises(FrozenInstanceError):
            frame.as_of = 0
        with self.assertRaises(FrozenInstanceError):
            frame.candles_15m[0].received_time = 0
        with self.assertRaises(FrozenInstanceError):
            frame.depth.bids[0].quantity_base = D(999)
        with self.assertRaises((TypeError, ValueError)):
            replace(frame, executable_ready=True)

    def test_policy_arguments_are_required_and_strictly_typed(self):
        with self.assertRaises(TypeError):
            FramePolicyV1()
        for args in ((True, 5, D(".2")), (5.0, 5, D(".2")), (-1, 5, D(".2")),
                     (5, 5, 0.2), (5, 5, D("NaN")), (5, 5, D("-.1"))):
            with self.subTest(args=args), self.assertRaises(FrameV1Error):
                FramePolicyV1(*args)
        self.failure(V1ErrorCode.INVALID_CONFIG, policy=None)

    def test_zero_policy_limits_are_explicitly_supported(self):
        quote = replace(self.inputs["quote"], event_time=903604, received_time=903604,
                        best_bid=D(100), best_ask=D(100))
        depth = replace(self.inputs["depth"], event_time=903604, received_time=903604)
        frame = self.build(quote=quote, depth=depth, policy=FramePolicyV1(0, 0, D(0)))
        self.assertIs(frame.quality, QualityV1.COMPLETE)
        self.assertEqual(frame.spread_pct, D(0))

    def test_missing_quote_and_depth_are_independent_and_not_zero_spread(self):
        frame = self.build(quote=None, depth=None)
        self.assertIs(frame.quality, QualityV1.INCOMPLETE)
        self.assertEqual(frame.quality_reasons,
                         (QualityReasonV1.MISSING_QUOTE, QualityReasonV1.MISSING_DEPTH))
        self.assertIsNone(frame.spread_pct)
        self.assertFalse(frame.depth_valid)
        self.assertEqual(frame.received_time, 903601)

    def test_unknown_source_times_remain_null_and_block_complete_quality(self):
        frame = self.build(quote=replace(self.inputs["quote"], event_time=None),
                           depth=replace(self.inputs["depth"], event_time=None))
        self.assertIsNone(frame.quote.event_time)
        self.assertIsNone(frame.depth.event_time)
        self.assertEqual(frame.quality_reasons, (QualityReasonV1.UNKNOWN_QUOTE_EVENT_TIME,
                                                QualityReasonV1.UNKNOWN_DEPTH_EVENT_TIME))
        self.assertIsNone(frame.spread_pct)
        self.assertFalse(frame.depth_valid)
        self.assertFalse(frame.executable_ready)

    def test_missing_receipt_snapshot_prices_are_not_exposed(self):
        frame = self.build(quote=replace(self.inputs["quote"], received_time=None),
                           depth=replace(self.inputs["depth"], received_time=None))
        self.assertIsNone(frame.quote)
        self.assertIsNone(frame.depth)
        self.assertIn(QualityReasonV1.MISSING_QUOTE_RECEIVED_TIME, frame.quality_reasons)
        self.assertIn(QualityReasonV1.MISSING_DEPTH_RECEIVED_TIME, frame.quality_reasons)
        self.assertEqual(frame.received_time, 903601)

    def test_age_boundary_is_inclusive_and_checks_source_age_despite_fresh_receipt(self):
        for name, stale_reason in (("quote", QualityReasonV1.STALE_QUOTE),
                                    ("depth", QualityReasonV1.STALE_DEPTH)):
            with self.subTest(name=name):
                boundary = replace(self.inputs[name], event_time=903599, received_time=903600)
                self.assertIs(self.build(**{name: boundary}).quality, QualityV1.COMPLETE)
                stale = replace(self.inputs[name], event_time=903598, received_time=903604)
                frame = self.build(**{name: stale})
                self.assertIn(stale_reason, frame.quality_reasons)
                self.assertIs(frame.quality, QualityV1.INCOMPLETE)
                self.assertIsNotNone(getattr(frame, name))  # Past diagnostic evidence is retained.
        frame = self.build(quote=replace(self.inputs["quote"], event_time=903598, received_time=903598),
                           depth=replace(self.inputs["depth"], event_time=903598, received_time=903598))
        self.assertEqual(frame.quality_reasons, (QualityReasonV1.STALE_QUOTE, QualityReasonV1.STALE_DEPTH))

    def test_future_snapshot_evidence_is_rejected_not_passed_to_a_frame(self):
        for name in ("quote", "depth"):
            for changes, code in ((dict(event_time=903605, received_time=903605), V1ErrorCode.FUTURE_EVENT_TIME),
                                  (dict(received_time=903605), V1ErrorCode.DATA_NOT_AVAILABLE)):
                with self.subTest(name=name, changes=changes):
                    self.failure(code, **{name: replace(self.inputs[name], **changes)})

    def test_timestamps_reject_bool_float_string_and_negative_values(self):
        for value in (True, 903604.0, "903604", -1):
            with self.subTest(value=value):
                self.failure(V1ErrorCode.INVALID_TIMESTAMP, as_of=value)
                for name in ("quote", "depth"):
                    for field in ("event_time", "received_time"):
                        self.failure(V1ErrorCode.INVALID_TIMESTAMP,
                                     **{name: replace(self.inputs[name], **{field: value})})

    def test_source_time_cannot_follow_its_receipt(self):
        for name in ("quote", "depth"):
            with self.subTest(name=name):
                self.failure(V1ErrorCode.INVALID_TIME_ORDER,
                             **{name: replace(self.inputs[name], event_time=903603, received_time=903602)})

    def test_identity_mismatches_never_expose_another_markets_snapshots(self):
        for name, reason in (("quote", QualityReasonV1.QUOTE_IDENTITY_MISMATCH),
                             ("depth", QualityReasonV1.DEPTH_IDENTITY_MISMATCH)):
            for key in ("symbol", "base_asset", "quote_asset"):
                with self.subTest(name=name, key=key):
                    frame = self.build(**{name: replace(self.inputs[name], **{key: "FOREIGN"})})
                    self.assertIn(reason, frame.quality_reasons)
                    self.assertIsNone(getattr(frame, name))
                    self.assertIs(frame.quality, QualityV1.INCOMPLETE)

    def test_invalid_quote_values_have_none_spread_not_float_coercion(self):
        for changes in ({"best_bid": D(0)}, {"best_bid": D(-1)}, {"best_ask": D(99)},
                        {"best_bid": D("NaN")}, {"best_bid": D("Infinity")},
                        {"best_bid": 99.9}, {"best_ask": True}):
            with self.subTest(changes=changes):
                frame = self.build(quote=replace(self.inputs["quote"], **changes))
                self.assertIsNone(frame.spread_pct)
                self.assertIsNone(frame.quote)
                self.assertIn(QualityReasonV1.INVALID_QUOTE, frame.quality_reasons)

    def test_depth_requires_positive_ordered_uncrossed_levels_on_both_sides(self):
        valid = self.inputs["depth"]
        bad_levels = (
            dict(bids=()), dict(asks=()), dict(bids=list(valid.bids)),
            dict(bids=tuple(reversed(valid.bids))), dict(asks=tuple(reversed(valid.asks))),
            dict(bids=(valid.bids[0], valid.bids[0])),
            dict(bids=(BookLevelV1(D(101), D(1)),)),
            dict(bids=(BookLevelV1(D(99), D(0)),)),
            dict(asks=(BookLevelV1(D("Infinity"), D(1)),)),
            dict(asks=(BookLevelV1(D(100), 1.0),)),
        )
        for changes in bad_levels:
            with self.subTest(changes=changes):
                frame = self.build(depth=replace(valid, **changes))
                self.assertIsNone(frame.depth)
                self.assertFalse(frame.depth_valid)
                self.assertIn(QualityReasonV1.INVALID_DEPTH, frame.quality_reasons)

    def test_spread_uses_mid_price_and_percent_units(self):
        quote = replace(self.inputs["quote"], best_bid=D(90), best_ask=D(110))
        frame = self.build(quote=quote, policy=FramePolicyV1(5, 5, D(20)))
        self.assertEqual(frame.spread_pct, D(20))
        self.assertIs(frame.quality, QualityV1.COMPLETE)

    def test_zero_spread_is_valid_and_ceiling_uses_exact_not_rounded_ratio(self):
        quote = replace(self.inputs["quote"], best_bid=D(100), best_ask=D(100))
        self.assertEqual(self.build(quote=quote).spread_pct, D(0))
        above = replace(self.inputs["quote"], best_ask=D("100.10000000000000000000000000000000000001"))
        frame = self.build(quote=above)
        self.assertEqual(frame.spread_pct, D("0.2"))
        self.assertIn(QualityReasonV1.SPREAD_LIMIT_EXCEEDED, frame.quality_reasons)
        self.assertIs(frame.quality, QualityV1.INCOMPLETE)

    def test_spread_repeating_ratio_has_fixed_precision_independent_of_caller(self):
        quote = replace(self.inputs["quote"], best_bid=D(1), best_ask=D(2))
        options = dict(quote=quote, policy=FramePolicyV1(5, 5, D(100)))
        expected = self.build(**options)
        self.assertEqual(expected.spread_pct, D("66.66666666666666666666666666666667"))
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.Emax = 2
            context.Emin = -2
            context.traps[Inexact] = True
            context.traps[InvalidOperation] = True
            context.clear_flags()
            self.assertEqual(self.build(**options), expected)
            self.assertFalse(any(context.flags.values()))

    def test_history_requires_actual_receipt_and_rejects_receipt_before_close_or_after_as_of(self):
        for name in ("candles_15m", "candles_1h"):
            history = self.inputs[name]
            first = history[0]
            for receipt, code in ((None, V1ErrorCode.MISSING_RECEIVED_TIME),
                                  (True, V1ErrorCode.INVALID_TIMESTAMP),
                                  (903604.0, V1ErrorCode.INVALID_TIMESTAMP),
                                  (903605, V1ErrorCode.DATA_NOT_AVAILABLE),
                                  (first.candle.timestamp, V1ErrorCode.INVALID_TIME_ORDER)):
                with self.subTest(name=name, receipt=receipt):
                    bad = (replace(first, received_time=receipt),) + history[1:]
                    self.failure(code, **{name: bad})

    def test_history_uses_existing_validator_and_preserves_failure_cause(self):
        history = self.inputs["candles_15m"]
        for changes, code in (({"completed": False}, ErrorCode.INCOMPLETE_CANDLE),
                              ({"volume": D(-1)}, ErrorCode.NEGATIVE_VOLUME),
                              ({"high": D(98)}, ErrorCode.INVALID_OHLC),
                              ({"timestamp": True}, ErrorCode.INVALID_TIMESTAMP)):
            with self.subTest(changes=changes):
                error = self.failure(code, candles_15m=changed_bar(history, **changes))
                self.assertIsInstance(error.__cause__, MarketDataError)
        self.failure(V1ErrorCode.HISTORY_IDENTITY_MISMATCH,
                     candles_15m=changed_bar(history, symbol="OTHER"))

    def test_missing_duplicate_and_unsorted_history_is_not_repaired(self):
        history = self.inputs["candles_1h"]
        for candles, code in ((history[:1] + history[2:], ErrorCode.MISSING_CANDLE),
                              ((history[0], history[0]) + history[2:], ErrorCode.DUPLICATE_TIMESTAMP),
                              ((history[1], history[0]) + history[2:], ErrorCode.OUT_OF_ORDER)):
            with self.subTest(code=code):
                self.failure(code, candles_1h=candles)

    def test_minimum_history_boundaries_and_extra_history(self):
        for name in ("candles_15m", "candles_1h"):
            history = self.inputs[name]
            self.failure(V1ErrorCode.INSUFFICIENT_HISTORY, **{name: history[1:]})
            self.failure(V1ErrorCode.INSUFFICIENT_HISTORY, **{name: ()})
            first = history[0]
            interval = first.candle.timeframe.value
            earlier = AvailableCandle(replace(first.candle, timestamp=first.candle.timestamp - interval),
                                      first.received_time - interval, first.source_id)
            self.assertIs(self.build(**{name: (earlier,) + history}).quality, QualityV1.COMPLETE)

    def test_freshness_uses_explicit_arbitrary_decision_time_without_grace_default(self):
        self.assertEqual(self.build(as_of=903605).event_time, 903600)
        self.failure(V1ErrorCode.STALE_HISTORY, as_of=904500)
        self.failure(V1ErrorCode.DATA_NOT_AVAILABLE, as_of=903600)

    def test_canonical_decimal_handles_integer_zeros_signed_zero_small_values_and_exponents(self):
        cases = {"1.00": "1", "100": "100", "100.000": "100", "0.0012300": "0.00123",
                 "-0.000": "0", "-0E+99999": "0", "1E+5": "100000", "1e-8": "0.00000001",
                 "-120.05000": "-120.05"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_decimal(D(raw)), expected)
        for bad in (1.0, True, D("NaN"), D("Infinity"), D("1e-10001")):
            with self.subTest(bad=bad), self.assertRaises(FrameV1Error):
                canonical_decimal(bad)

    def test_equivalent_decimal_representations_have_identical_snapshot_hashes(self):
        history = self.inputs["candles_15m"]
        changes = dict(open=D("100.000"), high=D("101.0"), low=D("99.00"),
                       close=D("100.0"), volume=D("1.000"))
        history = changed_bar(history, **changes)
        quote = replace(self.inputs["quote"], best_bid=D("99.90000"), best_ask=D("100.1000"))
        frame = self.build(candles_15m=history, quote=quote, policy=FramePolicyV1(5, 5, D(".2000")))
        self.assertEqual(frame.snapshot_hash, self.build().snapshot_hash)

    def test_negative_zero_and_unsigned_zero_have_identical_hashes(self):
        history = self.inputs["candles_15m"]
        unsigned = self.build(candles_15m=changed_bar(history, volume=D("0")))
        signed = self.build(candles_15m=changed_bar(history, volume=D("-0.000")))
        self.assertEqual(unsigned.snapshot_hash, signed.snapshot_hash)

    def test_fixed_canonical_fixture_and_hash_are_utf8_and_platform_independent(self):
        frame = self.build()
        path = Path(__file__).resolve().parent / "fixtures" / "marketframe_v1_synthetic.json"
        text = path.read_text(encoding="utf-8").rstrip("\r\n")
        self.assertEqual(text, canonical_frame_json(frame))
        self.assertEqual(sha256(text.encode("utf-8")).hexdigest(), EXPECTED_HASH)
        self.assertEqual(frame.snapshot_hash, EXPECTED_HASH)
        self.assertTrue(verify_snapshot_hash(frame))
        self.assertIn("synthetic-داده", text)
        self.assertNotIn("\\u062f", text)
        payload = json.loads(text)
        self.assertNotIn("snapshot_hash", payload)
        self.assertEqual(payload["candles_15m"][0]["candle"]["timeframe"], "M15")
        self.assertIs(type(payload["as_of"]), int)
        self.assertIs(payload["candles_15m"][0]["candle"]["completed"], True)

    def test_hash_covers_data_receipts_policy_quality_source_and_depth(self):
        original = self.build()
        history = self.inputs["candles_15m"]
        for options in (
            {"candles_15m": changed_bar(history, volume=D(2))},
            {"candles_15m": (replace(history[0], received_time=history[0].received_time + 1),) + history[1:]},
            {"candles_15m": (replace(history[0], source_id="OTHER-SOURCE"),) + history[1:]},
            {"as_of": 903605}, {"policy": FramePolicyV1(6, 5, D(".20"))},
            {"quote": replace(self.inputs["quote"], received_time=903603)},
            {"quote": None},
            {"depth": replace(self.inputs["depth"], bids=(BookLevelV1(D("99.9"), D(3)),))},
        ):
            with self.subTest(options=tuple(options)):
                self.assertNotEqual(self.build(**options).snapshot_hash, original.snapshot_hash)
        self.assertNotEqual(compute_snapshot_hash(replace(original, quality=QualityV1.INCOMPLETE)), original.snapshot_hash)

    def test_hash_ignores_itself_but_detects_tampering_and_bad_serialization(self):
        original = self.build()
        changed = replace(original, snapshot_hash="0" * 64)
        self.assertEqual(compute_snapshot_hash(changed), original.snapshot_hash)
        self.assertFalse(verify_snapshot_hash(changed))
        self.assertFalse(verify_snapshot_hash(replace(original, as_of=903605)))
        self.assertFalse(verify_snapshot_hash(replace(original, spread_pct=0.2)))
        self.assertFalse(verify_snapshot_hash(replace(original, as_of=True)))
        self.assertFalse(verify_snapshot_hash(replace(original, snapshot_hash=None)))

    def test_example_runs_from_unrelated_directory_without_strategy_or_execution(self):
        root = Path(__file__).resolve().parents[2]
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from wallex.market.frame_v1_example import main
main()
assert not any(n.startswith(('wallex.strategy', 'wallex.execution', 'wallex.risk')) for n in sys.modules)
"""
        with TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(root)],
                                    cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["snapshot_hash"], EXPECTED_HASH)
        self.assertEqual(summary["evidence_kind"], "synthetic_fixture")
        self.assertFalse(summary["executable_ready"])


if __name__ == "__main__":
    unittest.main()
