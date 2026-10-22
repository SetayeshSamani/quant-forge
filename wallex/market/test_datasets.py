"""Local temporary-file tests; all observations and instrument labels are synthetic."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from . import (
    DatasetError, DatasetErrorCode, ErrorCode, MarketDataError,
    Timeframe, TimestampUnit, load_dataset, normalize_candles,
)


FIXED_BYTES = (
    b'{"timestamp":0,"open":"100","high":"101","low":"99",'
    b'"close":"100","volume":"0","completed":true}\n'
)
FIXED_SHA256 = "77e051ca070487b74c2dc9a0904ce24ba6e95961dedcfbb4f2b0948b6e59edbd"


def row(timestamp=0, **changes):
    return dict(timestamp=timestamp, open="100", high="101", low="99",
                close="100", volume="0", completed=True) | changes


def encode_rows(*records):
    return ("\n".join(json.dumps(r, separators=(",", ":")) for r in records)).encode("utf-8")


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic.jsonl"

    def load(self, payload=FIXED_BYTES, **changes):
        self.path.write_bytes(payload)
        return self.read(**changes)

    def read(self, **changes):
        options = dict(symbol="TEST-BASE-QUOTE", timeframe=Timeframe.M15,
                       timestamp_unit=TimestampUnit.SECONDS, as_of=9000)
        return load_dataset(self.path, **(options | changes))

    def assert_failure(self, payload, code, *, line_number=None, **options):
        with self.assertRaises(DatasetError) as caught:
            self.load(payload, **options)
        error = caught.exception
        self.assertEqual((error.code, error.line_number), (code, line_number))
        return error

    def test_loads_known_bytes_with_exact_hash_and_metadata(self):
        result = self.load(as_of=900)
        self.assertEqual(result.sha256, FIXED_SHA256)
        self.assertEqual(result.candle_count, 1)
        self.assertEqual((result.first_open_at, result.last_open_at), (0, 0))
        self.assertEqual(result.symbol, "TEST-BASE-QUOTE")
        self.assertIs(result.timeframe, Timeframe.M15)
        self.assertIs(result.timestamp_unit, TimestampUnit.SECONDS)
        self.assertEqual(result.as_of, 900)
        candle, = result.candles
        self.assertEqual((candle.open, candle.high, candle.low, candle.close, candle.volume),
                         tuple(map(Decimal, ("100", "101", "99", "100", "0"))))

    def test_multirow_metadata_reports_open_times_not_close_times(self):
        result = self.load(encode_rows(row(900), row(1800), row(2700)))
        self.assertEqual(result.candle_count, 3)
        self.assertEqual((result.first_open_at, result.last_open_at), (900, 2700))

    def test_hash_and_candles_use_the_same_single_read_snapshot(self):
        # On-disk data differs from the supplied read snapshot. Neither a second
        # read nor reopening as text may supply the data or its digest.
        self.path.write_bytes(encode_rows(row(volume="9")))
        with patch.object(Path, "read_bytes", side_effect=[FIXED_BYTES, b"corrupt"]) as read:
            with patch.object(Path, "read_text", side_effect=AssertionError("second read")):
                result = self.read()
        read.assert_called_once_with()
        self.assertEqual(result.sha256, FIXED_SHA256)
        self.assertEqual(result.candles[0].volume, Decimal(0))

    def test_normalizer_receives_the_complete_batch_once(self):
        records = [row(), row(900), row(1800)]
        with patch(f"{load_dataset.__module__}.normalize_candles",
                   wraps=normalize_candles) as normalize:
            result = self.load(encode_rows(*records))
        normalize.assert_called_once_with(
            records, symbol="TEST-BASE-QUOTE", timeframe=Timeframe.M15,
            timestamp_unit=TimestampUnit.SECONDS, as_of=9000,
        )
        self.assertEqual(result.candle_count, 3)

    def test_same_file_and_settings_produce_equal_results(self):
        first = self.load(encode_rows(row(), row(900)))
        self.assertEqual(first, self.read())

    def test_settings_are_part_of_result_even_when_candles_and_hash_match(self):
        first = self.load(as_of=900)
        second = self.read(as_of=1800)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.candles, second.candles)
        self.assertNotEqual(first, second)

    def test_whitespace_and_line_endings_change_hash_not_candles(self):
        variants = [FIXED_BYTES.rstrip(b"\n"), FIXED_BYTES,
                    FIXED_BYTES.replace(b"\n", b"\r\n"),
                    b" \t" + FIXED_BYTES.replace(b'"open":', b'"open" : ')]
        results = [self.load(payload) for payload in variants]
        self.assertEqual(len({r.sha256 for r in results}), len(variants))
        self.assertTrue(all(r.candles == results[0].candles for r in results))

    def test_empty_file_has_empty_metadata_and_standard_empty_digest(self):
        result = self.load(b"", as_of=0)
        self.assertEqual(result.candles, ())
        self.assertEqual(result.candle_count, 0)
        self.assertIsNone(result.first_open_at)
        self.assertIsNone(result.last_open_at)
        self.assertEqual(result.sha256,
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_all_timeframes_and_explicit_timestamp_units(self):
        for timeframe in (Timeframe.M15, Timeframe.H1):
            seconds = self.load(encode_rows(row(0), row(timeframe.value)),
                                timeframe=timeframe, as_of=timeframe.value * 2)
            milliseconds = self.load(encode_rows(row(0), row(timeframe.value * 1000)),
                                     timeframe=timeframe, as_of=timeframe.value * 2,
                                     timestamp_unit=TimestampUnit.MILLISECONDS)
            with self.subTest(timeframe=timeframe):
                self.assertEqual(seconds.candles, milliseconds.candles)
                self.assertEqual(milliseconds.last_open_at, timeframe.value)
                self.assertIs(milliseconds.timestamp_unit, TimestampUnit.MILLISECONDS)

    def test_integer_json_prices_and_volume_are_accepted_exactly(self):
        result = self.load(encode_rows(row(open=100, high=101, low=99, close=100, volume=0)))
        self.assertEqual(result.candles[0].close, Decimal(100))
        self.assertEqual(result.candles[0].volume, Decimal(0))

    def test_blank_lines_are_not_skipped(self):
        for payload, line_number in ((b"\n", 1), (b" \t\r\n", 1),
                                     (FIXED_BYTES + b"\n", 2),
                                     (FIXED_BYTES + b" \t\n", 2)):
            with self.subTest(payload=payload):
                self.assert_failure(payload, DatasetErrorCode.BLANK_LINE,
                                    line_number=line_number)

    def test_invalid_utf8_reports_the_physical_line(self):
        error = self.assert_failure(FIXED_BYTES + b"\xff\n",
                                    DatasetErrorCode.INVALID_ENCODING, line_number=2)
        self.assertIsInstance(error.__cause__, UnicodeDecodeError)

    def test_utf8_bom_is_rejected(self):
        self.assert_failure(b"\xef\xbb\xbf" + FIXED_BYTES,
                            DatasetErrorCode.INVALID_ENCODING, line_number=1)

    def test_utf16_is_not_autodetected(self):
        self.assert_failure(FIXED_BYTES.decode("ascii").encode("utf-16"),
                            DatasetErrorCode.INVALID_ENCODING, line_number=1)

    def test_bad_json_and_extra_json_values_report_line(self):
        for bad in (b"{", b"{} {}", b'{"timestamp":01}', b'{"timestamp":0,}'):
            with self.subTest(bad=bad):
                error = self.assert_failure(FIXED_BYTES + bad,
                                            DatasetErrorCode.INVALID_JSON, line_number=2)
                self.assertIsInstance(error.__cause__, json.JSONDecodeError)

    def test_nonobject_records_are_rejected(self):
        for bad in (b"null", b"[]", b"123", b'"text"', b"true"):
            with self.subTest(bad=bad):
                self.assert_failure(FIXED_BYTES + bad, DatasetErrorCode.NON_OBJECT_RECORD,
                                    line_number=2)

    def test_duplicate_keys_are_rejected_including_escaped_names(self):
        for bad in (b'{"timestamp":0,"timestamp":900}',
                    b'{"timestamp":0,"time\\u0073tamp":900}',
                    b'{"nested":{"value":1,"value":2}}'):
            with self.subTest(bad=bad):
                self.assert_failure(FIXED_BYTES + bad, DatasetErrorCode.DUPLICATE_KEY,
                                    line_number=2)

    def test_float_and_nonfinite_json_tokens_are_rejected(self):
        for token in (b"100.0", b"1e2", b"1E-9999", b"NaN", b"Infinity", b"-Infinity"):
            for field in (b"timestamp", b"open", b"high", b"low", b"close", b"volume"):
                with self.subTest(token=token, field=field):
                    self.assert_failure(b'{"' + field + b'":' + token + b'}',
                                        DatasetErrorCode.INVALID_JSON_NUMBER, line_number=1)

    def test_decimal_strings_remain_exact(self):
        value = "100.000000000000000000000000000001"
        result = self.load(encode_rows(row(open=value, volume="1.25e-20")))
        self.assertEqual(result.candles[0].open, Decimal(value))
        self.assertEqual(result.candles[0].volume, Decimal("1.25e-20"))

    def test_read_failures_are_chained_and_have_no_line(self):
        with self.assertRaises(DatasetError) as caught:
            self.read()
        self.assertIs(caught.exception.code, DatasetErrorCode.READ_ERROR)
        self.assertIsNone(caught.exception.line_number)
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("test denied")):
            with self.assertRaises(DatasetError) as caught:
                self.read()
        self.assertIs(caught.exception.code, DatasetErrorCode.READ_ERROR)
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_validation_error_preserves_code_index_field_and_cause(self):
        error = self.assert_failure(encode_rows(row(), row(900, volume="-1")),
                                    ErrorCode.NEGATIVE_VOLUME, line_number=2)
        cause = error.__cause__
        self.assertIsInstance(cause, MarketDataError)
        self.assertEqual((error.code, error.index, error.field),
                         (cause.code, cause.index, cause.field))
        self.assertEqual((error.index, error.field), (1, "volume"))
        self.assertIn("line 2", str(error))

    def test_missing_and_extra_fields_use_normalizer_errors(self):
        missing = row(900)
        del missing["close"]
        error = self.assert_failure(encode_rows(row(), missing), ErrorCode.MISSING_FIELD,
                                    line_number=2)
        self.assertEqual(error.field, "close")
        self.assert_failure(encode_rows(row(symbol="OTHER")),
                            ErrorCode.UNEXPECTED_FIELD, line_number=1)

    def test_wrong_timestamp_completion_and_numeric_types_use_normalizer_errors(self):
        for changes, code, field in (
            ({"timestamp": True}, ErrorCode.INVALID_TIMESTAMP, "timestamp"),
            ({"completed": 1}, ErrorCode.INVALID_COMPLETION, "completed"),
            ({"volume": True}, ErrorCode.INVALID_NUMBER, "volume"),
            ({"volume": "NaN"}, ErrorCode.INVALID_NUMBER, "volume"),
        ):
            with self.subTest(changes=changes):
                error = self.assert_failure(encode_rows(row(**changes)), code, line_number=1)
                self.assertEqual(error.field, field)

    def test_cross_record_duplicates_order_and_gaps_are_rejected(self):
        cases = (([row(), row(900), row()], ErrorCode.DUPLICATE_TIMESTAMP, 3),
                 ([row(900), row()], ErrorCode.OUT_OF_ORDER, 2),
                 ([row(), row(1800)], ErrorCode.MISSING_CANDLE, 2))
        for records, code, line_number in cases:
            with self.subTest(code=code):
                self.assert_failure(encode_rows(*records), code, line_number=line_number)

    def test_open_and_future_candles_are_rejected(self):
        self.assert_failure(encode_rows(row(), row(900, completed=False)),
                            ErrorCode.INCOMPLETE_CANDLE, line_number=2)
        self.assert_failure(encode_rows(row(), row(900)), ErrorCode.FUTURE_CANDLE,
                            line_number=2, as_of=1799)
        self.assertEqual(self.load(encode_rows(row(), row(900)), as_of=1800).candle_count, 2)

    def test_config_error_has_no_fabricated_record_or_line(self):
        for payload in (b"", FIXED_BYTES):
            with self.subTest(payload=payload):
                error = self.assert_failure(payload, ErrorCode.INVALID_CONFIG, as_of=-1)
                self.assertIsNone(error.index)
                self.assertEqual(error.field, "as_of")
                self.assertIsInstance(error.__cause__, MarketDataError)

    def test_result_is_immutable_and_loader_does_not_modify_file(self):
        self.path.write_bytes(FIXED_BYTES)
        before = self.path.stat().st_mtime_ns
        result = self.read()
        self.assertEqual(self.path.read_bytes(), FIXED_BYTES)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertIsInstance(result.candles, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.sha256 = "changed"
        with self.assertRaises(FrozenInstanceError):
            result.candles[0].volume = Decimal(123)

    def test_unicode_line_separator_is_not_a_physical_jsonl_line(self):
        # A Unicode separator within a JSON value must reach the normalizer as
        # part of that value, not become a spurious third physical line.
        data = encode_rows(row(), row(900, volume="1\u20282")).replace(b"\\u2028", b"\xe2\x80\xa8")
        error = self.assert_failure(data, ErrorCode.INVALID_NUMBER, line_number=2)
        self.assertEqual(error.field, "volume")

    def test_bundled_fixture_is_loadable_synthetic_history(self):
        fixture = Path(__file__).parent / "fixtures" / "synthetic_m15.jsonl"
        result = load_dataset(fixture, symbol="SYNTHETIC-BASE-QUOTE",
                              timeframe=Timeframe.M15,
                              timestamp_unit=TimestampUnit.SECONDS, as_of=2700)
        self.assertEqual(result.candle_count, 3)
        self.assertEqual((result.first_open_at, result.last_open_at), (0, 1800))
        self.assertEqual(tuple(c.volume for c in result.candles),
                         (Decimal(0), Decimal("1.25"), Decimal(2)))


if __name__ == "__main__":
    unittest.main()
