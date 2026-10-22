"""Synthetic OHLCV fixtures and boundary/compatibility checks; no network."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, InvalidOperation, ROUND_UP, localcontext
from types import MappingProxyType
import unittest

from . import ErrorCode, MarketDataError, Timeframe, TimestampUnit, normalize_candles


D = Decimal
M15 = Timeframe.M15
H1 = Timeframe.H1


def row(timestamp=0, **changes):
    return dict(timestamp=timestamp, open="100", high="101", low="99",
                close="100", volume="0", completed=True) | changes


def normalize(records, **changes):
    options = dict(symbol="TEST-BASE-QUOTE", timeframe=M15,
                   timestamp_unit=TimestampUnit.SECONDS, as_of=9000)
    return normalize_candles(records, **(options | changes))


class NormalizationTests(unittest.TestCase):
    def assert_error(self, code, records, *, index=0, field=None, **options):
        with self.assertRaises(MarketDataError) as caught:
            normalize(records, **options)
        error = caught.exception
        self.assertEqual((error.code, error.index, error.field), (code, index, field))
        return error

    def test_normalizes_supported_numbers_without_rounding(self):
        data = [row(open=D("100.000000000000000000000000000001"),
                    high="1.01e2", low=99, close="+100.50", volume=".125")]
        candle, = normalize(data)
        self.assertEqual(candle.symbol, "TEST-BASE-QUOTE")
        self.assertIs(candle.timeframe, M15)
        self.assertEqual(candle.timestamp, 0)
        self.assertIs(candle.completed, True)
        self.assertEqual(candle.open, D("100.000000000000000000000000000001"))
        self.assertEqual(candle.volume, D("0.125"))
        for field in ("open", "high", "low", "close", "volume"):
            self.assertIsInstance(getattr(candle, field), D)

    def test_zero_volume_and_equal_ohlc_are_valid(self):
        for volume in (0, "0", D("0")):
            with self.subTest(volume=volume):
                candle, = normalize([row(open=1, high=1, low=1, close=1, volume=volume)])
                self.assertEqual(candle.volume, D(0))

    def test_seconds_and_milliseconds_produce_identical_output(self):
        seconds = [row(900), row(1800)]
        milliseconds = [row(900000), row(1800000)]
        self.assertEqual(normalize(seconds), normalize(
            milliseconds, timestamp_unit=TimestampUnit.MILLISECONDS))

    def test_timestamp_unit_is_used_literally_without_magnitude_guessing(self):
        data = [row(900000)]
        seconds, = normalize(data, as_of=901000)
        milliseconds, = normalize(data, as_of=901000,
                                   timestamp_unit=TimestampUnit.MILLISECONDS)
        self.assertEqual((seconds.timestamp, milliseconds.timestamp), (900000, 900))

    def test_both_timeframes_accept_close_exactly_at_cutoff(self):
        for timeframe in (M15, H1):
            with self.subTest(timeframe=timeframe):
                candle, = normalize([row()], timeframe=timeframe, as_of=timeframe.value)
                self.assertEqual(candle.timeframe, timeframe)

    def test_one_second_before_close_rejects_entire_batch(self):
        for timeframe in (M15, H1):
            with self.subTest(timeframe=timeframe):
                self.assert_error(ErrorCode.FUTURE_CANDLE, [row()], field="timestamp",
                                  timeframe=timeframe, as_of=timeframe.value - 1)

    def test_as_of_always_uses_seconds_even_for_millisecond_input(self):
        self.assert_error(ErrorCode.FUTURE_CANDLE, [row(900000)], field="timestamp",
                          timestamp_unit=TimestampUnit.MILLISECONDS, as_of=1799)
        self.assertEqual(normalize([row(900000)], as_of=1800,
                                   timestamp_unit=TimestampUnit.MILLISECONDS)[0].timestamp, 900)

    def test_subsecond_milliseconds_are_rejected_not_truncated(self):
        self.assert_error(ErrorCode.SUBSECOND_TIMESTAMP, [row(900001)],
                          field="timestamp", timestamp_unit=TimestampUnit.MILLISECONDS)

    def test_invalid_timestamp_types_are_rejected(self):
        for timestamp in (None, True, False, "0", 0.0, D(0), -900):
            with self.subTest(timestamp=timestamp):
                self.assert_error(ErrorCode.INVALID_TIMESTAMP, [row(timestamp)],
                                  field="timestamp")

    def test_unaligned_opens_rejected_for_both_timeframes(self):
        for timeframe, timestamp in ((M15, 1), (H1, 900)):
            with self.subTest(timeframe=timeframe):
                self.assert_error(ErrorCode.UNALIGNED_TIMESTAMP, [row(timestamp)],
                                  timeframe=timeframe, field="timestamp")

    def test_missing_fields_report_index_and_field(self):
        for field in row():
            data = row(900)
            del data[field]
            with self.subTest(field=field):
                self.assert_error(ErrorCode.MISSING_FIELD, [row(), data], index=1, field=field)

    def test_unknown_fields_are_not_silently_dropped(self):
        for field in ("symbol", "timeframe", "extra", 123):
            with self.subTest(field=field):
                data = row()
                data[field] = "unexpected"
                self.assert_error(ErrorCode.UNEXPECTED_FIELD, [data])

    def test_malformed_record_and_collection_are_rejected(self):
        for record in (None, 1, "row", [0, 1]):
            with self.subTest(record=record):
                self.assert_error(ErrorCode.INVALID_RECORD, [row(), record], index=1)
        for records in (None, {}, "rows", b"rows", bytearray(), iter([row()])):
            with self.subTest(records=records):
                self.assert_error(ErrorCode.INVALID_RECORDS, records, index=None)

    def test_float_bool_and_invalid_numeric_text_rejected_in_every_numeric_field(self):
        bad = (None, True, False, 100.0, "", " 100", "100 ", "1_00", "۱۲۳",
               "NaN", "sNaN", "Infinity", "1e", "--1", [], {},
               D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"),
               "1e999999999999999999999999999999")
        for field in ("open", "high", "low", "close", "volume"):
            for value in bad:
                with self.subTest(field=field, value=repr(value)):
                    self.assert_error(ErrorCode.INVALID_NUMBER, [row(**{field: value})],
                                      field=field)

    def test_nonpositive_prices_are_rejected(self):
        for field in ("open", "high", "low", "close"):
            for value in (0, "-0", D("-1")):
                with self.subTest(field=field, value=value):
                    self.assert_error(ErrorCode.NON_POSITIVE_PRICE,
                                      [row(**{field: value})], field=field)

    def test_negative_volume_is_rejected(self):
        self.assert_error(ErrorCode.NEGATIVE_VOLUME, [row(volume="-0.01")], field="volume")

    def test_inconsistent_ohlc_is_rejected(self):
        for changes in ({"high": "98"}, {"low": "102"},
                        {"open": "102"}, {"close": "98"}):
            with self.subTest(changes=changes):
                self.assert_error(ErrorCode.INVALID_OHLC, [row(**changes)], field="ohlc")

    def test_open_candle_rejects_batch_even_when_end_time_has_passed(self):
        data = [row(), row(900, completed=False)]
        before = deepcopy(data)
        error = self.assert_error(ErrorCode.INCOMPLETE_CANDLE, data,
                                  index=1, field="completed")
        self.assertIn("records[1].completed", str(error))
        self.assertEqual(data, before)

    def test_completion_flag_is_not_coerced(self):
        for completed in (None, 0, 1, "true", D(1)):
            with self.subTest(completed=completed):
                self.assert_error(ErrorCode.INVALID_COMPLETION, [row(completed=completed)],
                                  field="completed")

    def test_future_row_is_not_silently_dropped(self):
        self.assert_error(ErrorCode.FUTURE_CANDLE, [row(), row(900)], index=1,
                          field="timestamp", as_of=900)

    def test_duplicates_are_detected_including_nonadjacent_repeats(self):
        for data, index in (([row(), row()], 1), ([row(), row(900), row()], 2)):
            with self.subTest(index=index):
                self.assert_error(ErrorCode.DUPLICATE_TIMESTAMP, data,
                                  index=index, field="timestamp")

    def test_out_of_order_rows_are_not_sorted(self):
        self.assert_error(ErrorCode.OUT_OF_ORDER, [row(900), row()],
                          index=1, field="timestamp")

    def test_missing_intervals_are_not_filled(self):
        for timeframe in (M15, H1):
            with self.subTest(timeframe=timeframe):
                self.assert_error(ErrorCode.MISSING_CANDLE,
                                  [row(), row(timeframe.value * 2)], index=1,
                                  field="timestamp", timeframe=timeframe, as_of=10800)

    def test_price_gaps_between_valid_bars_are_allowed(self):
        second = row(900, open="200", high="201", low="199", close="200")
        self.assertEqual(len(normalize([row(), second])), 2)

    def test_invalid_config_is_rejected_even_with_empty_history(self):
        options = {
            "symbol": (None, "", " ", " TEST", "TEST X", 1),
            "timeframe": (None, 900, "M15", True),
            "timestamp_unit": (None, "seconds", 1000, True),
            "as_of": (None, True, -1, 900.0, D(900), "900"),
        }
        for field, invalid in options.items():
            for value in invalid:
                with self.subTest(field=field, value=value):
                    self.assert_error(ErrorCode.INVALID_CONFIG, [], index=None,
                                      field=field, **{field: value})

    def test_all_metadata_arguments_are_required(self):
        options = dict(symbol="TEST", timeframe=M15,
                       timestamp_unit=TimestampUnit.SECONDS, as_of=900)
        for field in options:
            with self.subTest(field=field), self.assertRaises(TypeError):
                normalize_candles([row()], **{k: v for k, v in options.items() if k != field})

    def test_empty_history_does_not_invent_data_or_imply_warmup(self):
        self.assertEqual(normalize([], as_of=0), ())

    def test_inputs_unchanged_and_results_immutable_and_repeatable(self):
        data = [row(), row(900)]
        before = deepcopy(data)
        result = normalize(data)
        self.assertEqual(data, before)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result, normalize(data))
        with self.assertRaises(FrozenInstanceError):
            result[0].timestamp = 123
        data[0]["close"] = "999"
        self.assertEqual(result[0].close, D(100))

    def test_read_only_mappings_and_tuple_input_are_supported(self):
        self.assertEqual(normalize((MappingProxyType(row()),)), normalize([row()]))

    def test_normalization_preserves_completed_history_prefix(self):
        data = [row(), row(900), row(1800)]
        self.assertEqual(normalize(data[:2], as_of=1800), normalize(data, as_of=2700)[:2])

    def test_decimal_context_does_not_affect_results_or_leak_flags(self):
        data = [row(open="100.000000000000000000000000000001")]
        expected = normalize(data)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.Emin = -2
            context.Emax = 2
            context.traps[Inexact] = True
            context.traps[InvalidOperation] = False
            context.clear_flags()
            self.assertEqual(normalize(data), expected)
            self.assert_error(ErrorCode.INVALID_NUMBER,
                              [row(volume="1e999999999999999999999999999999")], field="volume")
            self.assertFalse(any(context.flags.values()))
            self.assertEqual(context.prec, 2)
            self.assertFalse(context.traps[InvalidOperation])


class IndicatorCompatibilityTests(unittest.TestCase):
    def test_explicit_projection_into_existing_ema_and_atr_interfaces(self):
        # Only this consumer test imports strategy. The market module is independent.
        from wallex.strategy.indicators import Candle, Timeframe as IndicatorTimeframe, atr, ema

        for timeframe in (M15, H1):
            with self.subTest(timeframe=timeframe):
                cutoff = 50 * timeframe.value
                normalized = normalize([row(i * timeframe.value) for i in range(50)],
                                       timeframe=timeframe, as_of=cutoff)
                projected = tuple(Candle(
                    timestamp=c.timestamp, open=c.open, high=c.high, low=c.low,
                    close=c.close, completed=c.completed,
                ) for c in normalized)
                indicator_timeframe = IndicatorTimeframe[timeframe.name]
                fast = ema(projected, 20, timeframe=indicator_timeframe, as_of=cutoff)
                slow = ema(projected, 50, timeframe=indicator_timeframe, as_of=cutoff)
                ranges = atr(projected, timeframe=indicator_timeframe, as_of=cutoff)
                self.assertEqual(tuple(p.value for p in fast), (D(100),) * 31)
                self.assertEqual(tuple(p.value for p in slow), (D(100),))
                self.assertEqual(tuple(p.value for p in ranges), (D(2),) * 37)
                self.assertEqual((fast[0].closed_at, ranges[0].closed_at),
                                 (20 * timeframe.value, 14 * timeframe.value))
                self.assertEqual((fast[-1].closed_at, ranges[-1].closed_at), (cutoff, cutoff))


if __name__ == "__main__":
    unittest.main()
