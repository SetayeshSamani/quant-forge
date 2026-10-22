"""Synthetic OHLC fixtures; stop expectations are independently hand-calculated."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import unittest
from unittest.mock import patch

from . import stops as stops_module
from .indicators import Candle, IndicatorPoint
from .stops import StopAssessment, StopReason, assess_initial_stop

D = Decimal
STEP = 900
CUTOFF = 14 * STEP


def steady_history(count=14, *, price=D('100'), high=D('102'), low=D('98')):
    # Constant close inside each range: every TR is exactly high-low.
    return [Candle(index * STEP, price, high, low, price, True) for index in range(count)]


def structural_history(last_lows=(D('95'), D('94'), D('93'))):
    # All TRs=10. Earlier low=91 must be excluded from the three-bar lookback.
    lows = [D('91')] * 11 + list(last_lows)
    return [Candle(index * STEP, D('100'), low + D('10'), low, D('100'), True)
            for index, low in enumerate(lows)]


class StopCalculationTests(unittest.TestCase):
    def test_volatility_stop_is_lower_with_independent_reference_values(self):
        # ATR=4, last-three low=98: structural=97.6, volatility=94, risk=6.
        result = assess_initial_stop(steady_history(), entry_price=D('100'), as_of=CUTOFF)
        self.assertIsInstance(result, StopAssessment)
        self.assertTrue(result.valid)
        self.assertEqual(result.reasons, (StopReason.LEVELS_VALID,))
        self.assertEqual(result.entry_price, D('100'))
        self.assertEqual(result.lowest_low_last3, D('98'))
        self.assertEqual(result.atr14, D('4'))
        self.assertEqual(result.structural_stop, D('97.6'))
        self.assertEqual(result.volatility_stop, D('94'))
        self.assertEqual(result.initial_stop, D('94'))
        self.assertEqual(result.risk_per_unit, D('6'))

    def test_structural_stop_is_lower_with_independent_reference_values(self):
        # ATR=10, last-three low=93: structural=92, volatility=95, risk=18.
        result = assess_initial_stop(structural_history(), entry_price=D('110'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.lowest_low_last3, D('93'))
        self.assertEqual(result.atr14, D('10'))
        self.assertEqual(result.structural_stop, D('92'))
        self.assertEqual(result.volatility_stop, D('95'))
        self.assertEqual(result.initial_stop, D('92'))
        self.assertEqual(result.risk_per_unit, D('18'))

    def test_lowest_low_uses_only_latest_three_including_decision_candle(self):
        for lows in ((D('93'), D('95'), D('94')),
                     (D('95'), D('93'), D('94')),
                     (D('95'), D('94'), D('93'))):
            with self.subTest(last_three_lows=lows):
                data = structural_history(lows)
                self.assertEqual(data[10].low, D('91'))
                result = assess_initial_stop(data, entry_price=D('110'), as_of=CUTOFF)
                self.assertTrue(result.valid)
                self.assertEqual(result.lowest_low_last3, D('93'))
                self.assertEqual(result.initial_stop, D('92'))

    def test_equal_stop_candidates_are_valid(self):
        # Entry=103.6: 98-0.4 = 103.6-6 = 97.6; risk per unit is 6.
        result = assess_initial_stop(steady_history(), entry_price=D('103.6'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.structural_stop, D('97.6'))
        self.assertEqual(result.volatility_stop, D('97.6'))
        self.assertEqual(result.initial_stop, D('97.6'))
        self.assertEqual(result.risk_per_unit, D('6'))

    def test_exactly_fourteen_completed_m15_bars_is_enough(self):
        data = steady_history()
        self.assertEqual(len(data), 14)
        result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.atr14, D('4'))
        self.assertEqual(result.decision_closed_at, 14 * STEP)

    def test_latest_atr_uses_wilder_recurrence_and_matching_close_time(self):
        # Fourteen TRs=4; next TR=32, so current ATR=(4*13+32)/14=6.
        data = steady_history() + [Candle(CUTOFF, D('100'), D('116'), D('84'), D('100'), True)]
        result = assess_initial_stop(data, entry_price=D('100'), as_of=15 * STEP)
        self.assertTrue(result.valid)
        self.assertEqual(result.decision_closed_at, 15 * STEP)
        self.assertEqual(result.atr14, D('6'))
        self.assertEqual(result.lowest_low_last3, D('84'))
        self.assertEqual(result.structural_stop, D('83.4'))
        self.assertEqual(result.volatility_stop, D('91'))
        self.assertEqual(result.initial_stop, D('83.4'))
        self.assertEqual(result.risk_per_unit, D('16.6'))

    def test_explicit_entry_price_is_not_replaced_with_latest_close(self):
        result = assess_initial_stop(steady_history(), entry_price=D('90'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.entry_price, D('90'))
        self.assertEqual(result.structural_stop, D('97.6'))
        self.assertEqual(result.volatility_stop, D('84'))
        self.assertEqual(result.initial_stop, D('84'))
        self.assertEqual(result.risk_per_unit, D('6'))

    def test_no_exchange_tick_rounding_is_applied(self):
        result = assess_initial_stop(steady_history(), entry_price=D('100.12345'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.initial_stop, D('94.12345'))
        self.assertEqual(result.risk_per_unit, D('6'))

    def test_decimal_precision_beyond_default_twenty_eight_digits(self):
        price = D('100.0000000000000000000000000001')
        data = steady_history(price=price, high=D('102.0000000000000000000000000001'),
                              low=D('98.0000000000000000000000000001'))
        result = assess_initial_stop(data, entry_price=price, as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.structural_stop, D('97.6000000000000000000000000001'))
        self.assertEqual(result.initial_stop, D('94.0000000000000000000000000001'))
        self.assertEqual(result.risk_per_unit, D('6'))
        self.assertIsInstance(result.initial_stop, Decimal)

    def test_caller_decimal_context_does_not_change_levels(self):
        data = steady_history()
        expected = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.Emin = -9
            context.Emax = 9
            context.traps[Inexact] = True
            result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
            self.assertEqual(result, expected)
            self.assertTrue(result.valid)
            self.assertEqual(context.prec, 1)
            self.assertEqual(context.rounding, ROUND_UP)
            self.assertTrue(context.traps[Inexact])
        self.assertEqual(result, assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF))

    def test_result_is_immutable_and_input_is_not_modified(self):
        data = steady_history()
        before = tuple(data)
        result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
        self.assertEqual(tuple(data), before)
        self.assertIsInstance(result.reasons, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.valid = False
        with self.assertRaises(FrozenInstanceError):
            result.initial_stop = D('1')

    def test_valid_stop_does_not_approve_risk_or_execute_orders(self):
        # Exercise existing disabled foundations only; no exchange client exists.
        from wallex.infra import LiveTradingDisabledError, Runtime
        from wallex.risk import RiskLimits, RiskReason, evaluate_entry
        from .engine import Signal, evaluate

        result = assess_initial_stop(steady_history(), entry_price=D('100'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        risk = evaluate_entry(result, RiskLimits(D('1'), D('1000')))
        self.assertFalse(risk.allowed)
        self.assertEqual(risk.reason, RiskReason.INVALID_INPUT)
        runtime = Runtime()
        with self.assertRaises(LiveTradingDisabledError):
            runtime.execute_order(result)
        self.assertFalse(runtime.live_trading_enabled)
        self.assertEqual(evaluate().signal, Signal.NO_TRADE)


class StopSafetyTests(unittest.TestCase):
    def assert_failure_without_levels(self, result, *reasons):
        self.assertFalse(result.valid)
        self.assertEqual(result.reasons, reasons)
        for field in ('decision_closed_at', 'entry_price', 'lowest_low_last3', 'atr14',
                      'structural_stop', 'volatility_stop', 'initial_stop', 'risk_per_unit'):
            self.assertIsNone(getattr(result, field), field)

    def test_entry_price_must_be_explicit(self):
        with self.assertRaises(TypeError):
            assess_initial_stop(steady_history(), as_of=CUTOFF)

    def test_invalid_entry_price_fails_closed_without_coercion(self):
        data = steady_history()
        for value in (None, 100, 100.0, True, '100', D('0'), D('-1'),
                      D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity')):
            with self.subTest(entry_price=repr(value)):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=value, as_of=CUTOFF),
                                                   StopReason.INVALID_ENTRY_PRICE)

    def test_insufficient_atr_history_fails_closed(self):
        for count in (0, 2, 3, 12, 13):
            with self.subTest(count=count):
                self.assert_failure_without_levels(
                    assess_initial_stop(steady_history(count), entry_price=D('100'), as_of=CUTOFF),
                    StopReason.INSUFFICIENT_HISTORY)

    def test_zero_atr_is_rejected_without_levels(self):
        data = steady_history(high=D('100'), low=D('100'))
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.INVALID_ATR14)

    def test_zero_or_negative_volatility_stop_is_rejected(self):
        # ATR=4 => volatility stop is entry-6, zero at entry=6 and negative below.
        for entry in (D('6'), D('5')):
            with self.subTest(entry=entry):
                self.assert_failure_without_levels(
                    assess_initial_stop(steady_history(), entry_price=entry, as_of=CUTOFF),
                    StopReason.NON_POSITIVE_STOP)

    def test_zero_or_negative_structural_stop_is_rejected(self):
        # ATR=10: low=1 => structural=0; low=0.1 => structural=-0.9.
        for low, high in ((D('1'), D('11')), (D('0.1'), D('10.1'))):
            data = steady_history(price=D('5'), high=high, low=low)
            with self.subTest(low=low):
                self.assert_failure_without_levels(
                    assess_initial_stop(data, entry_price=D('20'), as_of=CUTOFF),
                    StopReason.NON_POSITIVE_STOP)

    def test_precision_collapse_cannot_produce_stop_at_entry_or_zero_risk(self):
        price = D('1e40')
        with localcontext() as context:
            context.prec = 80
            data = steady_history(price=price, high=price + D('2'), low=price - D('2'))
        # ATR=4, but both subtractions collapse to entry at 34-digit precision.
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=price, as_of=CUTOFF),
                                           StopReason.STOP_NOT_BELOW_ENTRY,
                                           StopReason.NON_POSITIVE_RISK_PER_UNIT)

    def test_missing_history_and_malformed_records_fail_closed(self):
        for data in (None, {}, 'candles', [None], iter(steady_history())):
            with self.subTest(data=data):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                                   StopReason.INVALID_DATA)
        self.assert_failure_without_levels(assess_initial_stop(entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.INVALID_DATA)

    def test_missing_time_bar_is_not_forward_filled(self):
        data = steady_history()
        del data[6]
        before = tuple(data)
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.INVALID_DATA)
        self.assertEqual(tuple(data), before)

    def test_incomplete_or_truthy_completion_flags_are_rejected(self):
        for flag in (False, None, 1, 'true'):
            data = steady_history()
            data[-1] = replace(data[-1], completed=flag)
            with self.subTest(flag=flag):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                                   StopReason.INVALID_DATA)

    def test_future_candle_is_rejected_instead_of_dropped(self):
        data = steady_history(15)
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.INVALID_DATA)

    def test_duplicate_and_out_of_order_timestamps_fail_closed(self):
        duplicate = steady_history()
        duplicate[6] = replace(duplicate[6], timestamp=duplicate[5].timestamp)
        unsorted = steady_history()
        unsorted[6], unsorted[7] = unsorted[7], unsorted[6]
        for data in (duplicate, unsorted):
            with self.subTest(data=data):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                                   StopReason.INVALID_DATA)

    def test_invalid_timestamp_types_and_alignment_fail_closed(self):
        original = steady_history()
        for timestamp in (None, True, -1, 1, 13 * STEP + 1, float(13 * STEP)):
            data = original[:-1] + [replace(original[-1], timestamp=timestamp)]
            with self.subTest(timestamp=timestamp):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF + STEP),
                                                   StopReason.INVALID_DATA)

    def test_h1_cadence_is_not_contiguous_m15_data(self):
        data = [replace(candle, timestamp=index * 3600) for index, candle in enumerate(steady_history())]
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=14 * 3600),
                                           StopReason.INVALID_DATA)

    def test_explicit_cutoff_boundary_and_decision_timestamp(self):
        data = steady_history()
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF - 1),
                                           StopReason.INVALID_DATA)
        result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
        self.assertTrue(result.valid)
        self.assertEqual(result.decision_closed_at, CUTOFF)
        self.assertEqual(result, assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF + 1))

    def test_old_valid_history_does_not_infer_wall_clock_freshness(self):
        data = steady_history()
        result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
        self.assertEqual(result, assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF + 365 * 86400))
        self.assertEqual(result.decision_closed_at, CUTOFF)

    def test_invalid_or_missing_as_of_is_not_inferred(self):
        data = steady_history()
        with self.assertRaises(TypeError):
            assess_initial_stop(data, entry_price=D('100'))
        for cutoff in (None, True, -1, float(CUTOFF), str(CUTOFF), D(CUTOFF)):
            with self.subTest(cutoff=cutoff):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=cutoff),
                                                   StopReason.INVALID_DATA)

    def test_no_look_ahead_from_later_low_or_atr(self):
        prefix = steady_history()
        earlier = assess_initial_stop(prefix, entry_price=D('100'), as_of=CUTOFF)
        later = prefix + [Candle(CUTOFF, D('100'), D('116'), D('84'), D('100'), True)]
        self.assert_failure_without_levels(assess_initial_stop(later, entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.INVALID_DATA)
        later_result = assess_initial_stop(later, entry_price=D('100'), as_of=15 * STEP)
        self.assertTrue(later_result.valid)
        self.assertEqual(later_result.initial_stop, D('83.4'))
        self.assertEqual(earlier.initial_stop, D('94'))
        self.assertEqual(earlier, assess_initial_stop(later[:14], entry_price=D('100'), as_of=CUTOFF))

    def test_invalid_nonfinite_or_nondecimal_ohlc_fails_closed(self):
        original = steady_history()
        for field in ('open', 'high', 'low', 'close'):
            for value in (None, 100, 100.0, True, '100', D('0'), D('-1'),
                          D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity')):
                data = original[:-1] + [replace(original[-1], **{field: value})]
                with self.subTest(field=field, value=repr(value)):
                    self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                                       StopReason.INVALID_DATA)

    def test_inconsistent_ohlc_fails_closed(self):
        original = steady_history()
        for changes in ({'open': D('103')}, {'high': D('97')}, {'low': D('103')}, {'close': D('97')}):
            data = original[:-1] + [replace(original[-1], **changes)]
            with self.subTest(changes=changes):
                self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                                   StopReason.INVALID_DATA)

    def test_unrepresentable_stop_arithmetic_fails_closed(self):
        self.assert_failure_without_levels(
            assess_initial_stop(steady_history(), entry_price=D('1e1000000'), as_of=CUTOFF),
            StopReason.ARITHMETIC_ERROR)

    def test_unrepresentable_atr_arithmetic_fails_closed(self):
        huge = D('1e1000000')
        data = [Candle(index * STEP, huge, D('2e1000000'), huge, huge, True) for index in range(14)]
        self.assert_failure_without_levels(assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF),
                                           StopReason.ARITHMETIC_ERROR)

    def test_missing_current_atr_timestamp_fails_closed(self):
        with patch.object(stops_module, 'atr', return_value=(IndicatorPoint(CUTOFF - STEP, D('4')),)):
            result = assess_initial_stop(steady_history(), entry_price=D('100'), as_of=CUTOFF)
        self.assert_failure_without_levels(result, StopReason.INDICATOR_ALIGNMENT_ERROR)

    def test_invalid_atr_outputs_are_rejected_without_levels(self):
        data = steady_history()
        for value in (D('0'), D('-1'), D('NaN'), D('sNaN'), D('Infinity'), None, 4.0):
            with self.subTest(value=repr(value)):
                with patch.object(stops_module, 'atr', return_value=(IndicatorPoint(CUTOFF, value),)):
                    result = assess_initial_stop(data, entry_price=D('100'), as_of=CUTOFF)
                self.assert_failure_without_levels(result, StopReason.INVALID_ATR14)


if __name__ == '__main__':
    unittest.main()
