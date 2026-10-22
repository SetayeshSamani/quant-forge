"""Independent hand-calculated references; all OHLC records are synthetic fixtures."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import unittest

from .indicators import (
    ATR_PERIOD, TP1_HOURLY_WARMUP, Candle, IndicatorArithmeticError,
    InsufficientHistory, InvalidCandleData, Timeframe, atr, ema,
    true_ranges, validate_candles, validate_tp1_warmup,
)

D = Decimal
M15 = Timeframe.M15
H1 = Timeframe.H1


def bar(timestamp=0, **changes):
    return replace(Candle(timestamp, D('100'), D('101'), D('99'), D('100'), True),
                   **changes)


def flat_series(values, timeframe=M15):
    result = []
    for index, value in enumerate(values):
        price = value if isinstance(value, Decimal) else D(value)
        result.append(Candle(index * timeframe.value, price, price, price, price, True))
    return result


def range_series(widths):
    # Center each candle at 100, so prior close is inside every range.
    return [bar(index * M15.value, high=D('100') + D(width) / D(2),
                low=D('100') - D(width) / D(2))
            for index, width in enumerate(widths)]


def values(points):
    return tuple(point.value for point in points)


class IndicatorCalculationTests(unittest.TestCase):
    def test_ema_sma_seed_and_recurrence(self):
        data = flat_series([1, 2, 3, 4, 8])
        # SMA(1,2,3)=2; alpha=1/2; next 3, then 5.5.
        result = ema(data, 3, timeframe=M15, as_of=4500)
        self.assertEqual(values(result), (D('2'), D('3'), D('5.5')))
        self.assertEqual(tuple(p.closed_at for p in result), (2700, 3600, 4500))

    def test_ema20(self):
        # Mean(1..20)=10.5; the next evenly spaced closes give 11.5, 12.5.
        result = ema(flat_series(range(1, 23)), 20, timeframe=M15, as_of=19800)
        self.assertEqual(values(result), (D('10.5'), D('11.5'), D('12.5')))

    def test_ema50(self):
        # Mean(1..50)=25.5; the next two EMA points are 26.5 and 27.5.
        result = ema(flat_series(range(1, 53)), 50, timeframe=M15, as_of=46800)
        self.assertEqual(values(result), (D('25.5'), D('26.5'), D('27.5')))

    def test_ema_period_one(self):
        data = flat_series([4, 2, 8])
        self.assertEqual(values(ema(data, 1, timeframe=M15, as_of=2700)),
                         (D('4'), D('2'), D('8')))

    def test_first_true_range_uses_high_minus_low(self):
        data = [bar(high=D('105'), low=D('95'), close=D('104'))]
        self.assertEqual(values(true_ranges(data, timeframe=M15, as_of=900)), (D('10'),))

    def test_true_range_without_price_gap(self):
        data = [bar(), bar(900, open=D('100'), high=D('103'), low=D('99'), close=D('102'))]
        self.assertEqual(values(true_ranges(data, timeframe=M15, as_of=1800)),
                         (D('2'), D('4')))

    def test_true_range_includes_up_and_down_price_gaps(self):
        data = [bar(),
                bar(900, open=D('113'), high=D('115'), low=D('112'), close=D('114')),
                bar(1800, open=D('89'), high=D('90'), low=D('87'), close=D('88'))]
        # max(3,15,12)=15; max(3,24,27)=27.
        self.assertEqual(values(true_ranges(data, timeframe=M15, as_of=2700)),
                         (D('2'), D('15'), D('27')))

    def test_atr14_seed_is_mean_of_fourteen_true_ranges(self):
        data = range_series(range(1, 15))
        # 1+...+14=105; 105/14=7.5. First-bar TR is included.
        result = atr(data, timeframe=M15, as_of=12600)
        self.assertEqual(values(result), (D('7.5'),))
        self.assertEqual(result[0].closed_at, 12600)
        self.assertEqual(ATR_PERIOD, 14)

    def test_atr14_wilder_recurrence(self):
        data = range_series([2] * 14 + [16, 31])
        # Seed=2; (2*13+16)/14=3; (3*13+31)/14=5.
        self.assertEqual(values(atr(data, timeframe=M15, as_of=14400)),
                         (D('2'), D('3'), D('5')))

    def test_flat_prices_have_zero_range_without_padding(self):
        data = flat_series([100] * 14)
        self.assertEqual(values(atr(data, timeframe=M15, as_of=12600)), (D('0'),))
        self.assertEqual(len(true_ranges(data, timeframe=M15, as_of=12600)), 14)

    def test_decimal_precision_beyond_default_twenty_eight_digits(self):
        data = flat_series([D('1.000000000000000000000000000001'),
                            D('1.000000000000000000000000000003')])
        result = ema(data, 2, timeframe=M15, as_of=1800)
        self.assertEqual(values(result), (D('1.000000000000000000000000000002'),))
        self.assertIsInstance(result[0].value, Decimal)

    def test_results_do_not_depend_on_callers_decimal_context(self):
        data = range_series([2] * 14 + [16, 31])
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_UP
            context.Emin = -9
            context.Emax = 9
            context.traps[Inexact] = True
            self.assertEqual(values(ema(flat_series([1, 2, 3, 4, 8]), 3,
                                        timeframe=M15, as_of=4500)),
                             (D('2'), D('3'), D('5.5')))
            self.assertEqual(values(atr(data, timeframe=M15, as_of=14400)),
                             (D('2'), D('3'), D('5')))
            self.assertEqual(values(ema(flat_series(range(1, 23)), 20,
                                        timeframe=M15, as_of=19800)),
                             (D('10.5'), D('11.5'), D('12.5')))
            self.assertEqual(context.prec, 3)
            self.assertEqual(context.rounding, ROUND_UP)
            self.assertTrue(context.traps[Inexact])

    def test_atr_nonterminating_fraction_uses_fixed_precision(self):
        data = range_series([2] * 14 + [3])
        # (2*13+3)/14 = 29/14, rounded to 34 significant decimal digits.
        with localcontext() as context:
            context.prec = 3
            context.traps[Inexact] = True
            result = atr(data, timeframe=M15, as_of=13500)
        self.assertEqual(result[-1].value,
                         D('2.071428571428571428571428571428571'))

    def test_unrepresentable_arithmetic_raises_explicit_error(self):
        data = flat_series([D('1e1000000')])
        with self.assertRaises(IndicatorArithmeticError):
            ema(data, 1, timeframe=M15, as_of=900)

    def test_input_records_and_results_are_immutable(self):
        data = flat_series([1, 2, 3])
        before = tuple(data)
        result = ema(data, 3, timeframe=M15, as_of=2700)
        self.assertEqual(tuple(data), before)
        with self.assertRaises(FrozenInstanceError):
            result[0].value = D('999')
        with self.assertRaises(FrozenInstanceError):
            data[0].completed = False


class IndicatorHistoryTests(unittest.TestCase):
    def test_insufficient_ema_history_has_explicit_counts(self):
        with self.assertRaises(InsufficientHistory) as caught:
            ema(flat_series(range(1, 20)), 20, timeframe=M15, as_of=17100)
        self.assertEqual((caught.exception.required, caught.exception.available), (20, 19))

    def test_insufficient_atr_history_has_explicit_counts(self):
        with self.assertRaises(InsufficientHistory) as caught:
            atr(range_series([2] * 13), timeframe=M15, as_of=11700)
        self.assertEqual((caught.exception.required, caught.exception.available), (14, 13))

    def test_empty_history_is_not_zero_padded(self):
        for call in (lambda: ema([], 20, timeframe=M15, as_of=0),
                     lambda: atr([], timeframe=M15, as_of=0),
                     lambda: true_ranges([], timeframe=M15, as_of=0)):
            with self.subTest(call=call), self.assertRaises(InsufficientHistory):
                call()

    def test_future_observations_do_not_change_prior_indicator_points(self):
        data = flat_series(list(range(1, 20)) + [999])
        prefix = data[:16]
        for function in (lambda bars, cutoff: ema(bars, 3, timeframe=M15, as_of=cutoff),
                         lambda bars, cutoff: atr(bars, timeframe=M15, as_of=cutoff),
                         lambda bars, cutoff: true_ranges(bars, timeframe=M15, as_of=cutoff)):
            with self.subTest(function=function):
                earlier = function(prefix, 14400)
                full = function(data, 18000)
                self.assertEqual(earlier, full[:len(earlier)])
                self.assertTrue(all(point.closed_at <= 14400 for point in earlier))

    def test_future_bars_are_rejected_at_an_earlier_cutoff(self):
        data = flat_series([100] * 16)
        for function in (lambda: ema(data, 3, timeframe=M15, as_of=13500),
                         lambda: atr(data, timeframe=M15, as_of=13500),
                         lambda: true_ranges(data, timeframe=M15, as_of=13500)):
            with self.subTest(function=function), self.assertRaises(InvalidCandleData):
                function()

    def test_hourly_bar_cannot_be_used_before_its_close(self):
        data = [bar()]
        for cutoff in (0, 900, 3599):
            with self.subTest(cutoff=cutoff), self.assertRaises(InvalidCandleData):
                ema(data, 1, timeframe=H1, as_of=cutoff)
        result = ema(data, 1, timeframe=H1, as_of=3600)
        self.assertEqual(result[0].closed_at, 3600)
        self.assertEqual(values(ema(data, 1, timeframe=M15, as_of=900)), (D('100'),))

    def test_next_hour_is_not_available_at_previous_hour_close(self):
        data = flat_series([100, 200], H1)
        with self.assertRaises(InvalidCandleData):
            ema(data, 1, timeframe=H1, as_of=3600)

    def test_h1_math_minimum_is_separate_from_tp1_warmup(self):
        data = flat_series([100] * 50, H1)
        self.assertEqual(len(ema(data, 50, timeframe=H1, as_of=180000)), 1)
        self.assertEqual(len(atr(data, timeframe=H1, as_of=180000)), 37)
        with self.assertRaises(InsufficientHistory) as caught:
            validate_tp1_warmup(data, as_of=180000)
        self.assertEqual((caught.exception.required, caught.exception.available), (250, 50))

    def test_tp1_requires_at_least_250_completed_hourly_bars(self):
        data = flat_series([100] * 250, H1)
        self.assertEqual(TP1_HOURLY_WARMUP, 250)
        self.assertEqual(validate_tp1_warmup(data, as_of=900000), tuple(data))
        with self.assertRaises(InsufficientHistory):
            validate_tp1_warmup(data[:249], as_of=900000)
        with self.assertRaises(InvalidCandleData):
            validate_tp1_warmup(data, as_of=899999)

    def test_quarter_hour_history_is_not_hourly_warmup(self):
        with self.assertRaises(InvalidCandleData):
            validate_tp1_warmup(flat_series([100] * 250), as_of=900000)


class IndicatorDataQualityTests(unittest.TestCase):
    def test_missing_or_malformed_sequences_are_rejected(self):
        for data in (None, 'candles', b'candles', {}, iter([bar()])):
            with self.subTest(data=data), self.assertRaises(InvalidCandleData):
                true_ranges(data, timeframe=M15, as_of=900)

    def test_malformed_observations_are_rejected(self):
        for observation in (None, {}, 1, 'candle'):
            with self.subTest(observation=observation), self.assertRaises(InvalidCandleData):
                true_ranges([observation], timeframe=M15, as_of=900)

    def test_invalid_periods_are_rejected(self):
        for period in (0, -1, True, False, 1.5, D('20'), '20', None):
            with self.subTest(period=period), self.assertRaises(InvalidCandleData):
                ema([bar()], period, timeframe=M15, as_of=900)

    def test_missing_malformed_and_nonfinite_ohlc_are_rejected(self):
        invalid = (None, 100, 100.0, True, '100', D('0'), D('-1'),
                   D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity'))
        for field in ('open', 'high', 'low', 'close'):
            for value in invalid:
                with self.subTest(field=field, value=repr(value)), self.assertRaises(InvalidCandleData):
                    true_ranges([bar(**{field: value})], timeframe=M15, as_of=900)

    def test_ohlc_consistency_is_enforced(self):
        for changes in ({'high': D('98')}, {'low': D('102')},
                        {'open': D('102')}, {'close': D('98')}):
            with self.subTest(changes=changes), self.assertRaises(InvalidCandleData):
                true_ranges([bar(**changes)], timeframe=M15, as_of=900)

    def test_duplicate_timestamps_are_rejected(self):
        with self.assertRaisesRegex(InvalidCandleData, 'Duplicate'):
            true_ranges([bar(), bar()], timeframe=M15, as_of=1800)

    def test_out_of_order_timestamps_are_rejected(self):
        with self.assertRaisesRegex(InvalidCandleData, 'out-of-order'):
            true_ranges([bar(900), bar()], timeframe=M15, as_of=1800)

    def test_incomplete_or_nonboolean_completion_is_rejected(self):
        for completed in (False, None, 1, 'true'):
            with self.subTest(completed=completed), self.assertRaises(InvalidCandleData):
                true_ranges([bar(completed=completed)], timeframe=M15, as_of=900)

    def test_invalid_timestamp_types_and_units_are_rejected(self):
        for timestamp in (None, True, 0.0, D('0'), '0', -900):
            with self.subTest(timestamp=timestamp), self.assertRaises(InvalidCandleData):
                true_ranges([bar(timestamp)], timeframe=M15, as_of=900)
        # A typical Unix millisecond value cannot pass a Unix-second cutoff.
        with self.assertRaises(InvalidCandleData):
            true_ranges([bar(1700000100000)], timeframe=M15, as_of=1700001000)

    def test_invalid_cutoff_is_rejected(self):
        for as_of in (None, True, 900.0, D('900'), '900', -1):
            with self.subTest(as_of=as_of), self.assertRaises(InvalidCandleData):
                true_ranges([bar()], timeframe=M15, as_of=as_of)

    def test_unsupported_or_implicit_timeframe_is_rejected(self):
        for timeframe in (None, 900, 3600, '15m', True):
            with self.subTest(timeframe=timeframe), self.assertRaises(InvalidCandleData):
                true_ranges([bar()], timeframe=timeframe, as_of=3600)

    def test_unaligned_open_times_are_rejected(self):
        for timestamp, timeframe in ((1, M15), (900, H1)):
            with self.subTest(timestamp=timestamp, timeframe=timeframe), self.assertRaises(InvalidCandleData):
                true_ranges([bar(timestamp)], timeframe=timeframe, as_of=7200)

    def test_m15_missing_time_bar_is_not_forward_filled(self):
        data = [bar(), bar(1800)]
        before = tuple(data)
        with self.assertRaisesRegex(InvalidCandleData, 'continuity gap'):
            true_ranges(data, timeframe=M15, as_of=2700)
        self.assertEqual(tuple(data), before)

    def test_h1_missing_time_bar_is_rejected(self):
        with self.assertRaisesRegex(InvalidCandleData, 'continuity gap'):
            true_ranges([bar(), bar(7200)], timeframe=H1, as_of=10800)

    def test_incomplete_bar_rejected_even_if_history_is_short(self):
        with self.assertRaises(InvalidCandleData):
            ema([bar(completed=False)], 50, timeframe=M15, as_of=900)

    def test_validation_returns_a_snapshot_without_invented_bars(self):
        data = [bar(), bar(900)]
        result = validate_candles(data, timeframe=M15, as_of=1800)
        self.assertEqual(result, tuple(data))
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


if __name__ == '__main__':
    unittest.main()
