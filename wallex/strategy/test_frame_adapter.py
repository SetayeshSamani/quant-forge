"""Synthetic, independently checked TP-1 diagnostics and rejection boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from enum import Enum
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import frame_adapter as adapter
from .engine import Signal, evaluate
from .indicators import Timeframe

D = Decimal
AS_OF = 1800000
SYMBOL = "SYNTHETIC-BASE-QUOTE"
R = adapter.DiagnosticReason


class SourceTimeframe(Enum):
    M15 = 900
    H1 = 3600


class SourceQuality(str, Enum):
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class SourceBar:
    symbol: str
    timeframe: SourceTimeframe
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    completed: bool = True


def frame_fixture(*, as_of=AS_OF, m15_count=21, h1_count=250):
    """Synthetic only: rising linear H1 series and a single M15 crossover.

    H1 closes are 100..349: SMA-seeded EMA20=349-9.5=339.5,
    EMA50=349-24.5=324.5, and the three-bar EMA20 reference is 336.5.
    M15 has twenty closes of 100 followed by 101, with every range 98..102.
    Previous EMA20=100, latest EMA20=100+2/21, ATR14=4.
    These independent histories do not claim cross-timeframe aggregation.
    """
    m15_end = as_of // 900 * 900
    h1_end = as_of // 3600 * 3600
    m15 = tuple(SourceBar(
        SYMBOL, SourceTimeframe.M15, m15_end - (m15_count-i)*900,
        D(100), D(102), D(98), D(101 if i == m15_count-1 else 100), D(1),
    ) for i in range(m15_count))
    h1 = tuple(SourceBar(
        SYMBOL, SourceTimeframe.H1, h1_end - (h1_count-i)*3600,
        D(100+i), D(101+i), D(99+i), D(100+i), D(4),
    ) for i in range(h1_count))
    return SimpleNamespace(
        schema_version="market-frame-proposal-v0", symbol=SYMBOL,
        base_asset="BASE", quote_asset="QUOTE", as_of=as_of,
        candles_15m=m15, candles_1h=h1,
        dataset_sha256_15m="a"*64, dataset_sha256_1h="b"*64,
        data_quality=SourceQuality.INCOMPLETE,
        quality_reasons=("QUOTE_UNAVAILABLE", "DEPTH_UNAVAILABLE",
                         "FRAME_TIME_SEMANTICS_PENDING", "SNAPSHOT_HASH_POLICY_PENDING",
                         "SHARED_SCHEMA_PENDING"),
        event_time=None, received_time=None, best_bid=None, best_ask=None,
        spread_pct=None, depth_valid=False, snapshot_hash=None,
    )


def change_bar(frame, slot, index, **changes):
    bars = list(getattr(frame, slot))
    bars[index] = replace(bars[index], **changes)
    setattr(frame, slot, tuple(bars))


class FrameAdapterTests(unittest.TestCase):
    def assert_rejected(self, frame, reason=None):
        result = adapter.assess_market_frame(frame)
        self.assertFalse(result.frame_valid)
        self.assertFalse(result.technical_eligible)
        self.assertFalse(result.executable_ready)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertIsNone(result.metadata)
        self.assertIsNone(result.trend)
        self.assertIsNone(result.pullback)
        self.assertIsNone(result.stop)
        self.assertIsNotNone(result.rejected_at)
        if reason is not None:
            self.assertIn(reason, result.reasons)
        return result

    def test_valid_projection_and_hand_checked_assessments(self):
        frame = frame_fixture()
        result = adapter.assess_market_frame(frame)
        self.assertTrue(result.frame_valid)
        self.assertTrue(result.technical_eligible)
        self.assertEqual(result.trend.ema20, D("339.5"))
        self.assertEqual(result.trend.ema50, D("324.5"))
        self.assertEqual(result.trend.ema20_three_bars_ago, D("336.5"))
        self.assertEqual(result.pullback.previous_ema20, D(100))
        self.assertEqual(result.pullback.current_ema20,
                         D("100.0952380952380952380952380952381"))
        self.assertEqual(result.pullback.current_atr14, D(4))
        self.assertEqual(result.metadata.m15.timeframe, Timeframe.M15)
        self.assertEqual(result.metadata.h1.timeframe, Timeframe.H1)
        self.assertEqual(len(result.metadata.m15.candles), 21)
        self.assertEqual(len(result.metadata.h1.candles), 250)

    def test_exact_decimal_metadata_volume_and_provenance_preserved(self):
        frame = frame_fixture()
        price = D("100.123456789012345678901234567890123456789")
        volume = D("0.0123456789012345678901234567890123456789")
        change_bar(frame, "candles_15m", 0, open=price, close=price, volume=volume)
        result = adapter.assess_market_frame(frame)
        self.assertTrue(result.frame_valid)
        meta = result.metadata
        self.assertEqual((meta.symbol, meta.base_asset, meta.quote_asset, meta.as_of),
                         (SYMBOL, "BASE", "QUOTE", AS_OF))
        self.assertEqual((meta.m15.dataset_sha256, meta.h1.dataset_sha256), ("a"*64, "b"*64))
        self.assertEqual(meta.m15.symbol, SYMBOL)
        self.assertEqual(meta.m15.candles[0].close.as_tuple(), price.as_tuple())
        self.assertEqual(meta.m15.volumes_base[0].as_tuple(), volume.as_tuple())
        for source, output in ((frame.candles_15m, meta.m15), (frame.candles_1h, meta.h1)):
            for bar, candle, vol in zip(source, output.candles, output.volumes_base):
                self.assertEqual((bar.timestamp, bar.completed), (candle.timestamp, candle.completed))
                for key in ("open", "high", "low", "close"):
                    self.assertEqual(getattr(bar, key).as_tuple(), getattr(candle, key).as_tuple())
                self.assertEqual(bar.volume.as_tuple(), vol.as_tuple())

    def test_same_as_of_including_explicit_provisional_stop(self):
        result = adapter.assess_market_frame(frame_fixture(), pe=D(110))
        self.assertEqual(result.metadata.as_of, AS_OF)
        self.assertEqual(result.trend.latest_closed_at, AS_OF)
        self.assertEqual(result.pullback.decision_closed_at, AS_OF)
        self.assertEqual(result.stop.decision_closed_at, AS_OF)

    def test_h1_uses_previous_completed_hour_at_intermediate_m15_cutoff(self):
        result = adapter.assess_market_frame(frame_fixture(as_of=AS_OF+900))
        self.assertTrue(result.frame_valid)
        self.assertEqual(result.pullback.decision_closed_at, AS_OF+900)
        self.assertEqual(result.trend.latest_closed_at, AS_OF)
        self.assertEqual(result.trend.slope_reference_closed_at, AS_OF-10800)

    def test_cutoff_between_m15_closes_uses_expected_completed_prefix(self):
        result = adapter.assess_market_frame(frame_fixture(as_of=AS_OF+899))
        self.assertTrue(result.frame_valid)
        self.assertEqual(result.metadata.as_of, AS_OF+899)
        self.assertEqual(result.pullback.decision_closed_at, AS_OF)

    def test_extra_valid_history_is_not_trimmed(self):
        result = adapter.assess_market_frame(frame_fixture(m15_count=22, h1_count=251))
        self.assertTrue(result.frame_valid)
        self.assertEqual((len(result.metadata.m15.candles), len(result.metadata.h1.candles)), (22, 251))

    def test_missing_and_insufficient_history(self):
        for slot, count in (("candles_15m", 20), ("candles_1h", 249)):
            for size in (0, count):
                with self.subTest(slot=slot, size=size):
                    frame = frame_fixture()
                    setattr(frame, slot, getattr(frame, slot)[-size:] if size else ())
                    self.assert_rejected(frame, R.INSUFFICIENT_HISTORY)

    def test_stale_contiguous_history_in_either_timeframe(self):
        for slot, seconds in (("candles_15m", 900), ("candles_1h", 3600)):
            with self.subTest(slot=slot):
                frame = frame_fixture()
                setattr(frame, slot, tuple(replace(bar, timestamp=bar.timestamp-seconds)
                                          for bar in getattr(frame, slot)))
                self.assert_rejected(frame, R.STALE_HISTORY)

    def test_incomplete_future_and_nonboolean_completion_rejected(self):
        for slot in ("candles_15m", "candles_1h"):
            for changes in ({"completed": False}, {"completed": 1}, {"timestamp": AS_OF}):
                with self.subTest(slot=slot, changes=changes):
                    frame = frame_fixture()
                    change_bar(frame, slot, -1, **changes)
                    self.assert_rejected(frame, R.INVALID_HISTORY)

    def test_one_second_before_close_rejects_instead_of_filtering(self):
        frame = frame_fixture()
        frame.as_of -= 1
        self.assert_rejected(frame, R.INVALID_HISTORY)

    def test_bad_timestamps_are_not_coerced_or_unit_guessed(self):
        for timestamp in (None, True, -1, 1, float(AS_OF), str(AS_OF), AS_OF*1000):
            with self.subTest(timestamp=timestamp):
                frame = frame_fixture()
                change_bar(frame, "candles_15m", -1, timestamp=timestamp)
                self.assert_rejected(frame, R.INVALID_HISTORY)

    def test_duplicate_gap_and_out_of_order_in_both_histories(self):
        for slot in ("candles_15m", "candles_1h"):
            for kind in ("duplicate", "gap", "unordered"):
                with self.subTest(slot=slot, kind=kind):
                    frame = frame_fixture()
                    bars = getattr(frame, slot)
                    changed = {"duplicate": (bars[0], bars[0])+bars[2:],
                               "gap": bars[:1]+bars[2:],
                               "unordered": (bars[1], bars[0])+bars[2:]}[kind]
                    setattr(frame, slot, changed)
                    self.assert_rejected(frame, R.INVALID_HISTORY)

    def test_ohlc_and_volume_domains_fail_closed_without_coercion(self):
        for field in ("open", "high", "low", "close", "volume"):
            invalid = [None, "100", 100, 100.0, True, D("NaN"), D("sNaN"), D("Infinity"), D(-1)]
            if field != "volume":
                invalid.append(D(0))
            for value in invalid:
                with self.subTest(field=field, value=value):
                    frame = frame_fixture()
                    change_bar(frame, "candles_15m", 0, **{field: value})
                    self.assert_rejected(frame, R.INVALID_HISTORY)
        frame = frame_fixture()
        change_bar(frame, "candles_1h", 0, high=D(98))
        self.assert_rejected(frame, R.INVALID_HISTORY)

    def test_zero_base_volume_is_valid(self):
        frame = frame_fixture()
        change_bar(frame, "candles_15m", 0, volume=D(0))
        self.assertTrue(adapter.assess_market_frame(frame).frame_valid)

    def test_timeframes_require_enum_name_and_exact_integer_seconds(self):
        wrong_name = Enum("WrongName", {"QUARTER_HOUR": 900}).QUARTER_HOUR
        wrong_value = Enum("WrongValue", {"M15": 900.0}).M15
        for slot in ("candles_15m", "candles_1h"):
            for value in (None, 900, "M15", SourceTimeframe.M15 if slot.endswith("1h") else SourceTimeframe.H1,
                          wrong_name, wrong_value):
                with self.subTest(slot=slot, value=value):
                    frame = frame_fixture()
                    change_bar(frame, slot, 0, timeframe=value)
                    self.assert_rejected(frame, R.TIMEFRAME_MISMATCH)

    def test_per_candle_symbol_mismatch_in_either_history(self):
        for slot in ("candles_15m", "candles_1h"):
            frame = frame_fixture()
            change_bar(frame, slot, 0, symbol="OTHER")
            self.assert_rejected(frame, R.CANDLE_IDENTITY_MISMATCH)

    def test_invalid_frame_and_missing_required_attributes(self):
        for frame in (None, {}, (), "frame", object()):
            self.assert_rejected(frame, R.INVALID_FRAME)
        for name in vars(frame_fixture()):
            with self.subTest(name=name):
                frame = frame_fixture()
                delattr(frame, name)
                self.assert_rejected(frame, R.INVALID_FRAME)

    def test_malformed_history_containers_and_candle_records(self):
        for slot in ("candles_15m", "candles_1h"):
            for bad in (None, [], "history", {}):
                with self.subTest(slot=slot, bad=bad):
                    frame = frame_fixture()
                    setattr(frame, slot, bad)
                    self.assert_rejected(frame, R.INVALID_HISTORY)
            for bad in (None, {}, "candle"):
                with self.subTest(slot=slot, candle=bad):
                    frame = frame_fixture()
                    bars = getattr(frame, slot)
                    setattr(frame, slot, (bad,)+bars[1:])
                    self.assert_rejected(frame, R.INVALID_FRAME)

    def test_unsupported_schema(self):
        for version in (None, 0, "market-frame-proposal-v1", "MarketFrame"):
            frame = frame_fixture()
            frame.schema_version = version
            self.assert_rejected(frame, R.UNSUPPORTED_SCHEMA)

    def test_invalid_identity_and_asset_consistency(self):
        for field in ("symbol", "base_asset", "quote_asset"):
            for value in (None, "", " ", "A B", "A\x00", "A\x7f", 1):
                frame = frame_fixture()
                setattr(frame, field, value)
                self.assert_rejected(frame, R.INVALID_IDENTITY)
        frame = frame_fixture()
        frame.base_asset = frame.quote_asset
        self.assert_rejected(frame, R.INVALID_IDENTITY)

    def test_invalid_as_of(self):
        for cutoff in (None, True, -1, str(AS_OF), float(AS_OF), D(AS_OF)):
            frame = frame_fixture()
            frame.as_of = cutoff
            self.assert_rejected(frame, R.INVALID_AS_OF)

    def test_dataset_hash_claims_preserved_but_malformed_hashes_rejected(self):
        for field in ("dataset_sha256_15m", "dataset_sha256_1h"):
            for value in (None, 1, "", "a"*63, "A"*64, "z"*64):
                frame = frame_fixture()
                setattr(frame, field, value)
                self.assert_rejected(frame, R.INVALID_PROVENANCE)

    def test_missing_quote_depth_time_hash_and_quality_reasons_preserved(self):
        result = adapter.assess_market_frame(frame_fixture())
        meta = result.metadata
        self.assertEqual(meta.data_quality, "INCOMPLETE")
        self.assertEqual(meta.quality_reasons, frame_fixture().quality_reasons)
        for name in ("event_time", "received_time", "best_bid", "best_ask", "spread_pct", "snapshot_hash"):
            self.assertIsNone(getattr(meta, name))
        self.assertIs(meta.depth_valid, False)
        for reason in (R.QUOTE_UNAVAILABLE, R.DEPTH_UNAVAILABLE, R.FRAME_TIME_UNAVAILABLE,
                       R.SNAPSHOT_HASH_UNAVAILABLE, R.FRAME_QUALITY_INCOMPLETE, R.SHARED_SCHEMA_UNAPPROVED):
            self.assertIn(reason, result.reasons)
        self.assertFalse(result.executable_ready)

    def test_forged_readiness_cannot_upgrade_v0(self):
        for name, value in (("data_quality", "COMPLETE"), ("best_ask", D(101)),
                            ("best_bid", D(100)), ("depth_valid", True), ("depth_valid", 0),
                            ("event_time", AS_OF), ("received_time", AS_OF),
                            ("spread_pct", D(0)), ("snapshot_hash", "c"*64),
                            ("quality_reasons", ()), ("quality_reasons", ["QUOTE_UNAVAILABLE"])):
            frame = frame_fixture()
            setattr(frame, name, value)
            self.assert_rejected(frame, R.INVALID_MARKET_EVIDENCE)

    def test_absent_pe_does_not_calculate_stop(self):
        with patch.object(adapter, "assess_initial_stop") as stop:
            result = adapter.assess_market_frame(frame_fixture())
        stop.assert_not_called()
        self.assertIn(R.PE_UNAVAILABLE, result.reasons)
        self.assertIsNone(result.stop)

    def test_invalid_pe_does_not_calculate_stop_or_coerce(self):
        for pe in (True, 100, "100", 100.0, D(0), D(-1), D("NaN"), D("sNaN"), D("Infinity")):
            with self.subTest(pe=pe), patch.object(adapter, "assess_initial_stop") as stop:
                result = adapter.assess_market_frame(frame_fixture(), pe=pe)
                self.assertTrue(result.frame_valid)
                self.assertIn(R.PE_INVALID, result.reasons)
                self.assertIsNone(result.stop)
                self.assertFalse(result.executable_ready)
                stop.assert_not_called()

    def test_explicit_pe_not_close_has_hand_checked_provisional_stop(self):
        result = adapter.assess_market_frame(frame_fixture(), pe=D(110))
        self.assertTrue(result.stop.valid)
        self.assertEqual(result.stop.entry_price, D(110))
        self.assertNotEqual(result.stop.entry_price, result.pullback.current_close)
        self.assertEqual(result.stop.lowest_low_last3, D(98))
        self.assertEqual(result.stop.atr14, D(4))
        self.assertEqual(result.stop.structural_stop, D("97.6"))
        self.assertEqual(result.stop.volatility_stop, D(104))
        self.assertEqual(result.stop.initial_stop, D("97.6"))
        self.assertEqual(result.stop.risk_per_unit, D("12.4"))
        self.assertIn(R.PE_PROVISIONAL, result.reasons)
        self.assertFalse(result.executable_ready)

    def test_invalid_proposed_stop_retains_no_usable_levels(self):
        result = adapter.assess_market_frame(frame_fixture(), pe=D(1))
        self.assertTrue(result.frame_valid)
        self.assertIn(R.STOP_INVALID, result.reasons)
        self.assertFalse(result.stop.valid)
        self.assertIsNone(result.stop.initial_stop)
        self.assertIsNone(result.stop.entry_price)

    def test_technical_success_and_valid_stop_never_enable_execution(self):
        result = adapter.assess_market_frame(frame_fixture(), pe=D(110))
        self.assertTrue(result.trend.eligible and result.pullback.eligible and result.technical_eligible)
        self.assertIn(R.TECHNICAL_CONDITIONS_MET, result.reasons)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertFalse(result.executable_ready)
        self.assertIs(evaluate().signal, Signal.NO_TRADE)

    def test_technical_failure_is_diagnostic_only(self):
        frame = frame_fixture()
        change_bar(frame, "candles_15m", -1, close=D(100))
        result = adapter.assess_market_frame(frame)
        self.assertTrue(result.frame_valid)
        self.assertTrue(result.trend.eligible)
        self.assertFalse(result.pullback.eligible or result.technical_eligible)
        self.assertIn(R.TECHNICAL_CONDITIONS_NOT_MET, result.reasons)

    def test_no_assessments_run_until_entire_frame_is_valid(self):
        frame = frame_fixture()
        change_bar(frame, "candles_1h", -1, completed=False)
        with patch.object(adapter, "assess_hourly_trend") as trend, \
                patch.object(adapter, "assess_pullback") as pullback, \
                patch.object(adapter, "assess_initial_stop") as stop:
            self.assert_rejected(frame)
        trend.assert_not_called()
        pullback.assert_not_called()
        stop.assert_not_called()

    def test_foreign_attribute_failure_discards_partial_evidence(self):
        class Broken:
            @property
            def schema_version(self):
                raise RuntimeError("foreign detail must not escape")
        result = self.assert_rejected(Broken(), R.INVALID_FRAME)
        self.assertNotIn("foreign detail", repr(result))

    def test_no_source_mutation_and_nested_result_is_immutable(self):
        frame = frame_fixture()
        before = deepcopy(frame)
        result = adapter.assess_market_frame(frame)
        self.assertEqual(frame, before)
        self.assertEqual(result, adapter.assess_market_frame(frame))
        for target, name, value in ((result, "executable_action", "BUY"),
                                    (result.metadata, "symbol", "OTHER"),
                                    (result.metadata.m15, "symbol", "OTHER"),
                                    (result.metadata.m15.candles[0], "close", D(1))):
            with self.assertRaises(FrozenInstanceError):
                setattr(target, name, value)
        with self.assertRaises((ValueError, TypeError)):
            replace(result, executable_ready=True)
        frame.symbol = "CHANGED-AFTER-ASSESSMENT"
        self.assertEqual(result.metadata.symbol, SYMBOL)

    def test_caller_decimal_context_does_not_change_diagnostics(self):
        frame = frame_fixture()
        expected = adapter.assess_market_frame(frame, pe=D(110))
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            self.assertEqual(adapter.assess_market_frame(frame, pe=D(110)), expected)

    def test_no_mandatory_market_import(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith('wallex.market'):
        raise AssertionError('Market import attempted')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from wallex.strategy.frame_adapter import assess_market_frame
assert not assess_market_frame().frame_valid
"""
        process = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout+process.stderr)


if __name__ == "__main__":
    unittest.main()
