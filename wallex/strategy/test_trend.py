"""TP-1 trend tests with synthetic H1 candles and independently known EMA values."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import unittest

from .indicators import Candle
from .trend import TrendAssessment, TrendReason, assess_hourly_trend

D = Decimal
HOUR = 3600
CUTOFF = 250 * HOUR


def hourly_series(closes):
    result = []
    for index, value in enumerate(closes):
        price = value if isinstance(value, Decimal) else D(value)
        result.append(Candle(index * HOUR, price, price, price, price, True))
    return result


def rising_history(count=250):
    return hourly_series(range(1, count + 1))


class TrendEligibilityTests(unittest.TestCase):
    def test_both_conditions_satisfied_with_hand_calculated_emas(self):
        # For closes 1..250, SMA seeding preserves the linear-ramp lag (N-1)/2.
        result = assess_hourly_trend(rising_history(), as_of=CUTOFF)
        self.assertIsInstance(result, TrendAssessment)
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, (TrendReason.TREND_CONDITIONS_MET,))
        self.assertEqual(result.ema20, D('240.5'))
        self.assertEqual(result.ema50, D('225.5'))
        self.assertEqual(result.ema20_three_bars_ago, D('237.5'))

    def test_ema20_below_ema50_rejects_despite_positive_slope(self):
        # After 300..54: EMA20=63.5, EMA50=78.5. Three closes at 70 make
        # EMA20 rise toward 70 and EMA50 fall toward 70, without crossing.
        data = hourly_series(list(range(300, 53, -1)) + [70, 70, 70])
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertLess(result.ema20, result.ema50)
        self.assertGreater(result.ema20, result.ema20_three_bars_ago)
        self.assertEqual(result.ema20_three_bars_ago, D('63.5'))
        self.assertEqual(result.reasons, (TrendReason.EMA20_NOT_ABOVE_EMA50,))

    def test_flat_ema20_slope_rejects_despite_ema20_above_ema50(self):
        # At close 247: EMA20=237.5, EMA50=222.5. Three closes at 237.5
        # keep EMA20 exactly flat while the slow EMA stays below it.
        data = hourly_series(list(range(1, 248)) + [D('237.5')] * 3)
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual(result.ema20, D('237.5'))
        self.assertEqual(result.ema20_three_bars_ago, D('237.5'))
        self.assertGreater(result.ema20, result.ema50)
        self.assertEqual(result.reasons, (TrendReason.EMA20_NOT_RISING_THREE_BARS,))

    def test_negative_slope_rejects_despite_ema20_above_ema50(self):
        # Three closes at 230 pull EMA20 down from 237.5 and EMA50 up
        # from 222.5. EMA20 remains above 230, and EMA50 below 230.
        data = hourly_series(list(range(1, 248)) + [230, 230, 230])
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertGreater(result.ema20, result.ema50)
        self.assertLess(result.ema20, result.ema20_three_bars_ago)
        self.assertEqual(result.reasons, (TrendReason.EMA20_NOT_RISING_THREE_BARS,))

    def test_equal_emas_and_flat_slope_fail_both_strict_conditions(self):
        result = assess_hourly_trend(hourly_series([100] * 250), as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual((result.ema20, result.ema50, result.ema20_three_bars_ago),
                         (D('100'), D('100'), D('100')))
        self.assertEqual(result.reasons, (TrendReason.EMA20_NOT_ABOVE_EMA50,
                                         TrendReason.EMA20_NOT_RISING_THREE_BARS))

    def test_downtrend_reports_both_reasons_in_stable_order(self):
        result = assess_hourly_trend(hourly_series(range(250, 0, -1)), as_of=CUTOFF)
        self.assertFalse(result.eligible)
        self.assertEqual((result.ema20, result.ema50, result.ema20_three_bars_ago),
                         (D('10.5'), D('25.5'), D('13.5')))
        self.assertEqual(result.reasons, (TrendReason.EMA20_NOT_ABOVE_EMA50,
                                         TrendReason.EMA20_NOT_RISING_THREE_BARS))

    def test_exactly_250_completed_hours_is_enough(self):
        data = rising_history()
        self.assertEqual(len(data), 250)
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertTrue(result.eligible)
        self.assertEqual(result.latest_closed_at, 250 * HOUR)
        self.assertEqual(result.slope_reference_closed_at, 247 * HOUR)

    def test_slope_uses_exactly_three_bars_not_the_last_one_or_two(self):
        # EMA20 starts at 237.5, rises above 243, then falls for two bars.
        # It still exceeds its value three bars ago, so the trend qualifies.
        data = hourly_series(list(range(1, 248)) + [300, 240, 230])
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertTrue(result.eligible)
        self.assertEqual(result.ema20_three_bars_ago, D('237.5'))
        self.assertGreater(result.ema20, D('237.5'))
        self.assertLess(result.ema20, D('242'))
        self.assertEqual(result.latest_closed_at - result.slope_reference_closed_at,
                         3 * HOUR)

    def test_more_than_250_bars_uses_latest_completed_hour(self):
        result = assess_hourly_trend(rising_history(251), as_of=251 * HOUR)
        self.assertTrue(result.eligible)
        self.assertEqual(result.ema20, D('241.5'))
        self.assertEqual(result.ema50, D('226.5'))
        self.assertEqual(result.ema20_three_bars_ago, D('238.5'))
        self.assertEqual(result.latest_closed_at, 251 * HOUR)

    def test_assessment_and_reasons_are_immutable(self):
        result = assess_hourly_trend(rising_history(), as_of=CUTOFF)
        self.assertIsInstance(result.reasons, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.eligible = False
        with self.assertRaises(FrozenInstanceError):
            result.ema20 = D('1')

    def test_caller_decimal_context_does_not_change_results(self):
        data = rising_history()
        expected = assess_hourly_trend(data, as_of=CUTOFF)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.Emin = -9
            context.Emax = 9
            context.traps[Inexact] = True
            actual = assess_hourly_trend(data, as_of=CUTOFF)
            self.assertEqual(actual, expected)
            self.assertEqual(context.prec, 2)
            self.assertEqual(context.rounding, ROUND_UP)
            self.assertTrue(context.traps[Inexact])
        self.assertIsInstance(actual.ema20, Decimal)
        self.assertEqual(assess_hourly_trend(data, as_of=CUTOFF), actual)

    def test_input_history_is_not_modified(self):
        data = rising_history()
        original = tuple(data)
        assess_hourly_trend(data, as_of=CUTOFF)
        self.assertEqual(tuple(data), original)


class TrendSafetyTests(unittest.TestCase):
    def assert_rejected_without_evidence(self, result, reason):
        self.assertFalse(result.eligible)
        self.assertEqual(result.reasons, (reason,))
        self.assertIsNone(result.latest_closed_at)
        self.assertIsNone(result.slope_reference_closed_at)
        self.assertIsNone(result.ema20)
        self.assertIsNone(result.ema50)
        self.assertIsNone(result.ema20_three_bars_ago)

    def test_insufficient_warmup_fails_closed(self):
        for count in (0, 20, 50, 249):
            with self.subTest(count=count):
                result = assess_hourly_trend(rising_history(count), as_of=CUTOFF)
                self.assert_rejected_without_evidence(result, TrendReason.INSUFFICIENT_HISTORY)

    def test_missing_or_malformed_history_fails_closed(self):
        for data in (None, {}, 'candles', [None], iter(rising_history())):
            with self.subTest(data=data):
                result = assess_hourly_trend(data, as_of=CUTOFF)
                self.assert_rejected_without_evidence(result, TrendReason.INVALID_DATA)
        self.assert_rejected_without_evidence(assess_hourly_trend(as_of=CUTOFF),
                                              TrendReason.INVALID_DATA)

    def test_missing_h1_bar_is_not_forward_filled(self):
        data = rising_history()
        del data[123]
        original = tuple(data)
        result = assess_hourly_trend(data, as_of=CUTOFF)
        self.assert_rejected_without_evidence(result, TrendReason.INVALID_DATA)
        self.assertEqual(tuple(data), original)

    def test_duplicate_timestamps_fail_closed(self):
        data = rising_history()
        data[100] = replace(data[100], timestamp=data[99].timestamp)
        self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                              TrendReason.INVALID_DATA)

    def test_out_of_order_timestamps_fail_closed(self):
        data = rising_history()
        data[100], data[101] = data[101], data[100]
        self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                              TrendReason.INVALID_DATA)

    def test_incomplete_or_truthy_completion_flag_fails_closed(self):
        for completed in (False, None, 1, 'true'):
            data = rising_history()
            data[-1] = replace(data[-1], completed=completed)
            with self.subTest(completed=completed):
                self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                                      TrendReason.INVALID_DATA)

    def test_future_h1_candle_is_rejected_not_silently_dropped(self):
        self.assert_rejected_without_evidence(
            assess_hourly_trend(rising_history(251), as_of=CUTOFF), TrendReason.INVALID_DATA)

    def test_cutoff_just_before_close_rejects_marked_completed_bar(self):
        self.assert_rejected_without_evidence(
            assess_hourly_trend(rising_history(), as_of=CUTOFF - 1), TrendReason.INVALID_DATA)

    def test_cutoff_at_close_or_after_has_the_same_completed_result(self):
        data = rising_history()
        at_close = assess_hourly_trend(data, as_of=CUTOFF)
        self.assertTrue(at_close.eligible)
        self.assertEqual(at_close, assess_hourly_trend(data, as_of=CUTOFF + 1))

    def test_partial_hour_is_unavailable_at_a_quarter_hour_cutoff(self):
        result = assess_hourly_trend(rising_history(), as_of=249 * HOUR + 900)
        self.assert_rejected_without_evidence(result, TrendReason.INVALID_DATA)

    def test_later_price_cannot_leak_into_an_earlier_assessment(self):
        prefix = rising_history()
        earlier = assess_hourly_trend(prefix, as_of=CUTOFF)
        later = hourly_series(range(1, 251)) + [
            Candle(CUTOFF, D('1000000'), D('1000000'), D('1000000'), D('1000000'), True)]
        self.assert_rejected_without_evidence(assess_hourly_trend(later, as_of=CUTOFF),
                                              TrendReason.INVALID_DATA)
        future_result = assess_hourly_trend(later, as_of=CUTOFF + HOUR)
        self.assertTrue(future_result.eligible)
        self.assertGreater(future_result.ema20, earlier.ema20)
        self.assertEqual(earlier, assess_hourly_trend(later[:250], as_of=CUTOFF))
        self.assertEqual(earlier.ema20, D('240.5'))
        self.assertLessEqual(earlier.latest_closed_at, CUTOFF)
        self.assertLessEqual(earlier.slope_reference_closed_at, CUTOFF)

    def test_completed_but_old_history_does_not_infer_wall_clock_freshness(self):
        data = rising_history()
        self.assertEqual(assess_hourly_trend(data, as_of=CUTOFF),
                         assess_hourly_trend(data, as_of=CUTOFF + 365 * 24 * HOUR))

    def test_as_of_is_required(self):
        with self.assertRaises(TypeError):
            assess_hourly_trend(rising_history())

    def test_invalid_cutoff_fails_closed(self):
        data = rising_history()
        for cutoff in (None, True, -1, float(CUTOFF), str(CUTOFF), D(CUTOFF)):
            with self.subTest(cutoff=cutoff):
                self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=cutoff),
                                                      TrendReason.INVALID_DATA)

    def test_invalid_ohlc_values_fail_closed_without_coercion(self):
        original = rising_history()
        invalid = (None, 250, 250.0, True, '250', D('0'), D('-1'),
                   D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity'))
        for field in ('open', 'high', 'low', 'close'):
            for value in invalid:
                data = original[:-1] + [replace(original[-1], **{field: value})]
                with self.subTest(field=field, value=repr(value)):
                    self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                                          TrendReason.INVALID_DATA)

    def test_inconsistent_ohlc_fails_closed(self):
        original = rising_history()
        for changes in ({'open': D('251')}, {'high': D('249')},
                        {'low': D('251')}, {'close': D('249')}):
            data = original[:-1] + [replace(original[-1], **changes)]
            with self.subTest(changes=changes):
                self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                                      TrendReason.INVALID_DATA)

    def test_invalid_timestamps_fail_closed(self):
        original = rising_history()
        for timestamp in (None, True, -1, 1, 249 * HOUR + 900, float(249 * HOUR)):
            data = original[:-1] + [replace(original[-1], timestamp=timestamp)]
            with self.subTest(timestamp=timestamp):
                self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF + HOUR),
                                                      TrendReason.INVALID_DATA)

    def test_quarter_hour_cadence_cannot_satisfy_hourly_warmup(self):
        data = [replace(candle, timestamp=index * 900)
                for index, candle in enumerate(rising_history())]
        self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                              TrendReason.INVALID_DATA)

    def test_unrepresentable_indicator_arithmetic_fails_closed(self):
        data = hourly_series([D('1e1000000')] * 250)
        self.assert_rejected_without_evidence(assess_hourly_trend(data, as_of=CUTOFF),
                                              TrendReason.INDICATOR_ARITHMETIC_ERROR)


if __name__ == '__main__':
    unittest.main()
