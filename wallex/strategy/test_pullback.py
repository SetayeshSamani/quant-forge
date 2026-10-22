"""Synthetic OHLC fixtures with independent hand-calculated crossover references."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import unittest
from unittest.mock import patch

from . import pullback as pullback_module
from .indicators import Candle, IndicatorPoint
from .pullback import PullbackAssessment, PullbackReason, assess_pullback

D = Decimal
STEP = 900
CUTOFF = 21 * STEP


def flat_history(count=20, *, half_range=D('7')):
    return [Candle(index * STEP, D('100'), D('100') + half_range,
                   D('100') - half_range, D('100'), True) for index in range(count)]


def strict_cross_history():
    # First 19 TRs=14. Bar 20: close=79, TR=28, ATR=15.
    # SMA20=(19*100+79)/20=98.95. Bar 21: TR=29, ATR=(15*13+29)/14=16.
    # Current EMA=98.95+(2/21)*(100-98.95)=99.05; distance=0.95.
    return flat_history(19) + [
        Candle(19 * STEP, D('100'), D('107'), D('79'), D('79'), True),
        Candle(20 * STEP, D('79'), D('108'), D('79'), D('100'), True),
    ]


def equality_boundary_history(count=20):
    # Prior closes=100, EMA=100, TR/ATR=18. Latest close=121, TR=32.
    # EMA=102, distance=19, ATR=(18*13+32)/14=19: inclusive ATR boundary.
    return flat_history(count, half_range=D('9')) + [
        Candle(count * STEP, D('100'), D('121'), D('89'), D('121'), True)]


class PullbackCalculationTests(unittest.TestCase):
    def test_valid_upward_cross_with_previous_close_strictly_below_ema(self):
        result = assess_pullback(strict_cross_history(), as_of=CUTOFF)
        self.assertIsInstance(result, PullbackAssessment)
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, (PullbackReason.PULLBACK_CONDITIONS_MET,))
        self.assertEqual(result.previous_close, D('79'))
        self.assertEqual(result.previous_ema20, D('98.95'))
        self.assertEqual(result.current_close, D('100'))
        self.assertEqual(result.current_ema20, D('99.05'))
        self.assertEqual(result.current_atr14, D('16'))
        self.assertEqual(result.distance_from_ema20, D('0.95'))

    def test_previous_close_exactly_equal_to_ema_is_allowed(self):
        # Prior TR/ATR=28; latest TR=21 => current ATR=27.5.
        data = flat_history(20, half_range=D('14')) + [
            Candle(20 * STEP, D('100'), D('121'), D('100'), D('121'), True)]
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertTrue(result.eligible)
        self.assertEqual(result.previous_close, D('100'))
        self.assertEqual(result.previous_ema20, D('100'))
        self.assertEqual(result.current_ema20, D('102'))
        self.assertEqual(result.distance_from_ema20, D('19'))
        self.assertEqual(result.current_atr14, D('27.5'))

    def test_previous_close_above_ema_rejects_otherwise_qualifying_current_bar(self):
        # SMA20=101.05. Next close 121 gives EMA=102.95 and distance=18.05.
        # Prior ATR=15, current TR=85 => ATR=20, so only condition A fails.
        data = flat_history(19) + [
            Candle(19 * STEP, D('100'), D('121'), D('93'), D('121'), True),
            Candle(20 * STEP, D('121'), D('150'), D('65'), D('121'), True)]
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.previous_ema20, D('101.05'))
        self.assertEqual(result.current_ema20, D('102.95'))
        self.assertEqual(result.current_atr14, D('20'))
        self.assertEqual(result.distance_from_ema20, D('18.05'))
        self.assertEqual(result.reasons, (PullbackReason.PREVIOUS_CLOSE_ABOVE_EMA20,))

    def test_current_close_exactly_equal_to_ema_is_not_allowed(self):
        result = assess_pullback(flat_history(21), as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.current_close, D('100'))
        self.assertEqual(result.current_ema20, D('100'))
        self.assertEqual(result.distance_from_ema20, D('0'))
        self.assertIn(PullbackReason.CURRENT_CLOSE_NOT_ABOVE_EMA20, result.reasons)
        self.assertIn(PullbackReason.DISTANCE_NOT_POSITIVE, result.reasons)

    def test_current_close_below_ema_is_not_allowed(self):
        data = flat_history(20) + [
            Candle(20 * STEP, D('100'), D('107'), D('79'), D('79'), True)]
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.current_ema20, D('98'))
        self.assertEqual(result.distance_from_ema20, D('-19'))
        self.assertIn(PullbackReason.CURRENT_CLOSE_NOT_ABOVE_EMA20, result.reasons)
        self.assertIn(PullbackReason.DISTANCE_NOT_POSITIVE, result.reasons)

    def test_distance_above_atr_rejects_an_upward_cross(self):
        # Prior ATR=14; current TR=28 => ATR=15, less than distance 19.
        data = flat_history(20) + [
            Candle(20 * STEP, D('100'), D('121'), D('93'), D('121'), True)]
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.current_ema20, D('102'))
        self.assertEqual(result.current_atr14, D('15'))
        self.assertEqual(result.distance_from_ema20, D('19'))
        self.assertEqual(result.reasons, (PullbackReason.DISTANCE_EXCEEDS_ATR14,))

    def test_distance_exactly_equal_to_current_atr_is_allowed(self):
        result = assess_pullback(equality_boundary_history(), as_of=CUTOFF)
        self.assertTrue(result.eligible)
        self.assertEqual(result.current_close, D('121'))
        self.assertEqual(result.current_ema20, D('102'))
        self.assertEqual(result.current_atr14, D('19'))
        self.assertEqual(result.distance_from_ema20, D('19'))

    def test_indicator_evidence_matches_current_and_previous_close_times(self):
        # Previous ATR is 18, current ATR is 19. Using the previous ATR would
        # incorrectly reject this boundary. EMA output starts later than ATR.
        result = assess_pullback(equality_boundary_history(), as_of=CUTOFF)
        self.assertEqual(result.decision_closed_at, 21 * STEP)
        self.assertEqual(result.previous_closed_at, 20 * STEP)
        self.assertEqual(result.previous_ema20, D('100'))
        self.assertEqual(result.current_ema20, D('102'))
        self.assertEqual(result.current_atr14, D('19'))
        self.assertTrue(result.eligible)

    def test_longer_history_uses_latest_bar_not_a_fixed_output_index(self):
        result = assess_pullback(equality_boundary_history(25), as_of=26 * STEP)
        self.assertTrue(result.eligible)
        self.assertEqual(result.decision_closed_at, 26 * STEP)
        self.assertEqual(result.previous_closed_at, 25 * STEP)
        self.assertEqual(result.current_ema20, D('102'))
        self.assertEqual(result.current_atr14, D('19'))

    def test_zero_atr_and_flat_prices_never_qualify(self):
        result = assess_pullback(flat_history(21, half_range=D('0')), as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.current_atr14, D('0'))
        self.assertEqual(result.distance_from_ema20, D('0'))
        self.assertIn(PullbackReason.ATR14_NOT_POSITIVE, result.reasons)

    def test_decimal_context_and_distance_are_independent_of_caller(self):
        data = equality_boundary_history()
        expected = assess_pullback(data, as_of=CUTOFF)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.Emin = -9
            context.Emax = 9
            context.traps[Inexact] = True
            result = assess_pullback(data, as_of=CUTOFF)
            self.assertEqual(result, expected)
            self.assertTrue(result.eligible)
            self.assertEqual(context.prec, 1)
            self.assertTrue(context.traps[Inexact])
            self.assertEqual(context.rounding, ROUND_UP)
        self.assertIsInstance(result.distance_from_ema20, Decimal)

    def test_prices_beyond_twenty_eight_significant_digits_remain_decimal(self):
        anchor = D('100.0000000000000000000000000001')
        data = [Candle(index * STEP, anchor,
                       D('109.0000000000000000000000000001'),
                       D('91.0000000000000000000000000001'), anchor, True)
                for index in range(20)]
        data.append(Candle(20 * STEP, anchor,
                           D('121.0000000000000000000000000001'),
                           D('89.0000000000000000000000000001'),
                           D('121.0000000000000000000000000001'), True))
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertTrue(result.eligible)
        self.assertEqual(result.previous_ema20, anchor)
        self.assertEqual(result.current_ema20, D('102.0000000000000000000000000001'))
        self.assertEqual(result.distance_from_ema20, D('19'))
        self.assertEqual(result.current_atr14, D('19'))

    def test_assessment_is_immutable_and_input_history_is_unchanged(self):
        data = strict_cross_history()
        before = tuple(data)
        result = assess_pullback(data, as_of=CUTOFF)
        self.assertEqual(tuple(data), before)
        self.assertIsInstance(result.reasons, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.eligible = False
        with self.assertRaises(FrozenInstanceError):
            result.current_ema20 = D('1')
        self.assertEqual(result, assess_pullback(data, as_of=CUTOFF))


class PullbackSafetyTests(unittest.TestCase):
    def assert_rejected_without_evidence(self, result, reason):
        self.assertFalse(result.eligible)
        self.assertEqual(result.reasons, (reason,))
        for field in ('decision_closed_at', 'previous_closed_at', 'current_close',
                      'previous_close', 'current_ema20', 'previous_ema20',
                      'current_atr14', 'distance_from_ema20'):
            self.assertIsNone(getattr(result, field), field)

    def test_insufficient_history_including_single_ema20_value(self):
        for count in (0, 1, 14, 19, 20):
            with self.subTest(count=count):
                self.assert_rejected_without_evidence(
                    assess_pullback(flat_history(count), as_of=CUTOFF),
                    PullbackReason.INSUFFICIENT_HISTORY)

    def test_missing_history_and_malformed_records_fail_closed(self):
        for data in (None, {}, 'candles', [None], iter(strict_cross_history())):
            with self.subTest(data=data):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                                      PullbackReason.INVALID_DATA)
        self.assert_rejected_without_evidence(assess_pullback(as_of=CUTOFF),
                                              PullbackReason.INVALID_DATA)

    def test_missing_candle_rejected_without_forward_fill(self):
        data = strict_cross_history()
        del data[10]
        original = tuple(data)
        self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                              PullbackReason.INVALID_DATA)
        self.assertEqual(tuple(data), original)

    def test_incomplete_or_truthy_completion_flag_is_rejected(self):
        for flag in (False, None, 1, 'true'):
            data = strict_cross_history()
            data[-1] = replace(data[-1], completed=flag)
            with self.subTest(flag=flag):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                                      PullbackReason.INVALID_DATA)

    def test_future_bar_is_rejected_instead_of_dropped(self):
        data = equality_boundary_history() + [
            Candle(CUTOFF, D('100'), D('100'), D('80'), D('80'), True)]
        self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                              PullbackReason.INVALID_DATA)

    def test_wrong_timestamp_alignment_and_types_are_rejected(self):
        original = strict_cross_history()
        for timestamp in (None, True, -1, 1, 20 * STEP + 1, float(20 * STEP)):
            data = original[:-1] + [replace(original[-1], timestamp=timestamp)]
            with self.subTest(timestamp=timestamp):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF + STEP),
                                                      PullbackReason.INVALID_DATA)

    def test_duplicate_and_unsorted_timestamps_are_rejected(self):
        duplicate = strict_cross_history()
        duplicate[10] = replace(duplicate[10], timestamp=duplicate[9].timestamp)
        unsorted = strict_cross_history()
        unsorted[10], unsorted[11] = unsorted[11], unsorted[10]
        for data in (duplicate, unsorted):
            with self.subTest(data=data):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                                      PullbackReason.INVALID_DATA)

    def test_exact_cutoff_boundary(self):
        data = strict_cross_history()
        self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF - 1),
                                              PullbackReason.INVALID_DATA)
        at_close = assess_pullback(data, as_of=CUTOFF)
        self.assertTrue(at_close.eligible)
        self.assertEqual(at_close.decision_closed_at, CUTOFF)
        self.assertEqual(at_close, assess_pullback(data, as_of=CUTOFF + 1))

    def test_decision_timestamp_is_candle_close_not_later_as_of(self):
        result = assess_pullback(strict_cross_history(), as_of=CUTOFF + 7 * STEP)
        self.assertTrue(result.eligible)
        self.assertEqual(result.decision_closed_at, CUTOFF)
        self.assertEqual(result.previous_closed_at, CUTOFF - STEP)

    def test_no_look_ahead_from_later_observations(self):
        prefix = equality_boundary_history()
        earlier = assess_pullback(prefix, as_of=CUTOFF)
        later = prefix + [Candle(CUTOFF, D('100'), D('100'), D('80'), D('80'), True)]
        self.assert_rejected_without_evidence(assess_pullback(later, as_of=CUTOFF),
                                              PullbackReason.INVALID_DATA)
        self.assertFalse(assess_pullback(later, as_of=CUTOFF + STEP).eligible)
        self.assertTrue(earlier.eligible)
        self.assertEqual(earlier, assess_pullback(later[:21], as_of=CUTOFF))
        self.assertEqual(earlier.current_ema20, D('102'))

    def test_as_of_is_required_and_invalid_cutoffs_fail_closed(self):
        data = strict_cross_history()
        with self.assertRaises(TypeError):
            assess_pullback(data)
        for cutoff in (None, True, -1, float(CUTOFF), str(CUTOFF), D(CUTOFF)):
            with self.subTest(cutoff=cutoff):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=cutoff),
                                                      PullbackReason.INVALID_DATA)

    def test_invalid_nonfinite_or_nondecimal_ohlc_fails_closed(self):
        original = strict_cross_history()
        invalid = (None, 100, 100.0, True, '100', D('0'), D('-1'),
                   D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity'))
        for field in ('open', 'high', 'low', 'close'):
            for value in invalid:
                data = original[:-1] + [replace(original[-1], **{field: value})]
                with self.subTest(field=field, value=repr(value)):
                    self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                                          PullbackReason.INVALID_DATA)

    def test_inconsistent_ohlc_fails_closed(self):
        original = strict_cross_history()
        for changes in ({'open': D('109')}, {'high': D('78')},
                        {'low': D('109')}, {'close': D('78')}):
            data = original[:-1] + [replace(original[-1], **changes)]
            with self.subTest(changes=changes):
                self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                                      PullbackReason.INVALID_DATA)

    def test_h1_cadence_is_not_accepted_as_contiguous_m15_history(self):
        data = [replace(candle, timestamp=index * 3600)
                for index, candle in enumerate(strict_cross_history())]
        self.assert_rejected_without_evidence(assess_pullback(data, as_of=21 * 3600),
                                              PullbackReason.INVALID_DATA)

    def test_unrepresentable_indicator_arithmetic_fails_closed(self):
        huge = D('1e1000000')
        data = [Candle(index * STEP, huge, huge, huge, huge, True) for index in range(21)]
        self.assert_rejected_without_evidence(assess_pullback(data, as_of=CUTOFF),
                                              PullbackReason.INDICATOR_ARITHMETIC_ERROR)

    def test_missing_current_indicator_timestamp_fails_closed(self):
        # Safety-injection only: numerical reference tests above use real indicators.
        with patch.object(pullback_module, 'atr', return_value=(IndicatorPoint(CUTOFF - STEP, D('19')),)):
            result = assess_pullback(equality_boundary_history(), as_of=CUTOFF)
        self.assert_rejected_without_evidence(result, PullbackReason.INDICATOR_ALIGNMENT_ERROR)

    def test_invalid_atr_output_fails_closed(self):
        data = equality_boundary_history()
        for value in (D('-1'), D('NaN'), D('sNaN'), D('Infinity'), None, 19.0):
            with self.subTest(value=repr(value)):
                with patch.object(pullback_module, 'atr', return_value=(IndicatorPoint(CUTOFF, value),)):
                    result = assess_pullback(data, as_of=CUTOFF)
                self.assert_rejected_without_evidence(result, PullbackReason.INDICATOR_ARITHMETIC_ERROR)


if __name__ == '__main__':
    unittest.main()
