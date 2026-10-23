"""Synthetic offline examples modeled on supplied QA; no real dataset or HTTP."""

from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wallex.market import gap_aware as gap, history


def wire(stamps):
    count = len(stamps)
    return dict(s="ok", t=list(stamps), o=["10"] * count, h=["12"] * count,
                l=["9"] * count, c=["11"] * count, v=["0.1"] * count)


def encoded(data):
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def analyze(data, start=0, end=900, **options):
    return gap.analyze_minutes(encoded(data), symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
                               start=start, end=end, as_of=end, **options)


class GapAwareTests(unittest.TestCase):
    def assert_research_only(self, report):
        self.assertFalse(report["fully_validated"] or report["executable_ready"])
        self.assertEqual(report["executable_action"], "NO_TRADE")
        self.assertFalse(report["source"]["volume_unit_verified"])

    def test_complete_bucket_preserves_zero_volume_prices_and_exact_decimal_sum(self):
        data = wire(range(0, 900, 60))
        for index in range(15):
            for key in ("o", "h", "l", "c"):
                data[key][index] = "82107.07" if index < 5 else "82464.32"
            data["v"][index] = "0" if index < 5 else "0.125"
        body = encoded(data)
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            report = analyze(data)
        candle = report["segments"][0]["candles"][0]
        self.assertEqual(tuple(candle[key] for key in ("open", "high", "low", "close", "volume")),
                         (Decimal("82107.07"), Decimal("82464.32"), Decimal("82107.07"), Decimal("82464.32"), Decimal("1.250")))
        self.assertEqual(report["counts"], dict(expected_m1=15, observed_m1=15, missing_m1=0,
                                               zero_volume_m1=5, complete_m15=1, incomplete_m15=0, segments=1))
        self.assertEqual(report["zero_volume_m1_timestamps"], [0, 60, 120, 180, 240])
        self.assertEqual(report["source"]["sha256"], sha256(body).hexdigest())
        self.assertEqual(candle["volume_semantics"], "UNVERIFIED")
        self.assertEqual(report["source"]["price_policy"], "AS_RECORDED_ALL_PRESENT_MINUTES")
        self.assertIn("ZERO_VOLUME_PRICE_SEMANTICS_UNVERIFIED", report["warnings"])
        self.assertEqual(report, analyze(data))
        self.assert_research_only(report)
        attested = analyze(data, volume_unit="base")
        self.assertEqual(attested["source"]["volume_semantics"], "CALLER_ATTESTED_BASE")
        self.assert_research_only(attested)

    def test_supplied_qa_pattern_has_597_minutes_38_buckets_and_two_separate_runs(self):
        start, end = 1791828000, 1791864000  # Synthetic 10-hour window around the supplied gaps.
        absent = [1791845940, 1791846000, 1791846060]
        data = wire([stamp for stamp in range(start, end, 60) if stamp not in absent])
        positive_offsets = {5, 6, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
        for index, stamp in enumerate(data["t"]):
            if 1791842400 <= stamp < 1791846000:
                offset = (stamp - 1791842400) // 60
                if offset not in positive_offsets:
                    data["v"][index] = "0"
                    for key in ("o", "h", "l", "c"): data[key][index] = "82107.07"
                else:
                    for key in ("o", "h", "l", "c"): data[key][index] = "82464.32"
        report = analyze(data, start=start, end=end)
        self.assertEqual((report["counts"]["observed_m1"], report["counts"]["complete_m15"],
                          report["counts"]["incomplete_m15"], report["counts"]["zero_volume_m1"]), (597, 38, 2, 47))
        self.assertEqual(report["missing_m1_timestamps"], absent)
        self.assertEqual([(item["open_at"], item["missing_timestamps"]) for item in report["incomplete_buckets"]],
                         [(1791845100, absent[:1]), (1791846000, absent[1:])])
        self.assertEqual([segment["candle_count"] for segment in report["segments"]], [19, 19])
        self.assertEqual(report["segments"][0]["end_close_at"], 1791845100)
        self.assertEqual(report["segments"][1]["start_open_at"], 1791846900)
        self.assertEqual(report["status"], "PARTIAL")
        self.assert_research_only(report)
        with self.assertRaisesRegex(ValueError, "M1_COVERAGE_OR_SPACING_INVALID"):
            history.decode_minutes(encoded(data), symbol="BTCUSDT", start=start, end=end)

    def test_single_multiple_edge_and_empty_gaps_never_emit_incomplete_candles(self):
        cases = ((0, 2700, [900], [0, 1800], 1, 2),
                 (0, 2700, [60, 120], [900, 1800], 1, 1),
                 (60, 1860, [], [900], 2, 1),
                 (0, 1800, list(range(0, 1800, 60)), [], 2, 0))
        for start, end, absent, emitted, incomplete, segments in cases:
            with self.subTest(range=(start, end), missing=len(absent)):
                data = wire([stamp for stamp in range(start, end, 60) if stamp not in absent])
                report = analyze(data, start=start, end=end)
                self.assertEqual([bar["timestamp"] for segment in report["segments"] for bar in segment["candles"]], emitted)
                self.assertEqual(report["missing_m1_timestamps"], absent)
                self.assertEqual(report["counts"]["incomplete_m15"], incomplete)
                self.assertEqual(report["counts"]["segments"], segments)
                for segment in report["segments"]:
                    stamps = [bar["timestamp"] for bar in segment["candles"]]
                    self.assertEqual(stamps, list(range(segment["start_open_at"], segment["end_close_at"], 900)))
                if start == 60:
                    self.assertEqual(report["incomplete_buckets"][0]["outside_expected_range"], [0])
                self.assert_research_only(report)
        all_zero = wire(range(0, 1800, 60))
        all_zero["v"] = ["0"] * 30
        report = analyze(all_zero, end=1800)
        self.assertEqual((report["counts"]["zero_volume_m1"], report["counts"]["missing_m1"], report["counts"]["complete_m15"]), (30, 0, 2))
        self.assertTrue(all(bar["volume"] == 0 for bar in report["segments"][0]["candles"]))

    def test_invalid_source_or_configuration_fails_without_dropping_bad_rows(self):
        for case in ("duplicate", "unordered", "misaligned", "out-of-range", "bool", "float", "length", "missing-field",
                     "ohlc", "negative-volume", "nonfinite", "null-price", "incomplete", "symbol", "status", "resource"):
            data = wire(range(0, 1800, 60))
            if case == "duplicate": data["t"][1] = 0
            elif case == "unordered": data["t"].reverse()
            elif case == "misaligned": data["t"][1] = 61
            elif case == "out-of-range": data["t"][-1] = 1800
            elif case == "bool": data["t"][0] = False
            elif case == "float": data["t"][0] = 0.0
            elif case == "length": data["v"].pop()
            elif case == "missing-field": del data["o"]
            elif case == "ohlc": data["l"][0] = "13"
            elif case == "negative-volume": data["v"][0] = "-1"
            elif case == "nonfinite": data["c"][0] = "NaN"
            elif case == "null-price": data["o"][0] = None
            elif case == "incomplete": data["completed"] = [True] * 29 + [False]
            elif case == "symbol": data["symbol"] = "OTHERUSDT"
            elif case == "status": data["s"] = "no_data"
            else: data["v"][0] = "1e101"
            with self.subTest(case=case), self.assertRaises(ValueError):
                analyze(data, end=1800)
        body = encoded(wire(range(0, 900, 60)))
        base = dict(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", start=0, end=900, as_of=900)
        for changes in ({"start":1}, {"end":899}, {"as_of":899}, {"as_of":None}, {"start":False},
                        {"end":15*86400,"as_of":15*86400}, {"symbol":"ETHUSDT"}, {"volume_unit":"quote"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                gap.analyze_minutes(body, **(base | changes))
        for malformed in (b'{"s":"ok","s":"ok"}', b'not-json', b'\xff', b' ' * (history.MAX_BYTES + 1)):
            with self.subTest(size=len(malformed)), self.assertRaises(ValueError):
                gap.analyze_minutes(malformed, **base)
        # Invalid data in a bucket with a gap must still fail, not be skipped.
        data = wire(range(60, 1800, 60))
        data["h"][0] = "1"
        with self.assertRaises(ValueError): analyze(data, end=1800)

    def test_cli_is_explicit_read_only_bounded_and_reports_only_local_evidence(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            body = encoded(wire(range(0, 900, 60)))
            path.write_bytes(body)
            args = ["--analyze", "--input", str(path), "--symbol", "BTCUSDT", "--base", "BTC", "--quote", "USDT",
                    "--start", "0", "--end", "900", "--as-of", "900", "--summary-only"]
            output = StringIO()
            with patch.object(history, "public_get") as get, patch.object(history, "build_opener") as opener, redirect_stdout(output):
                gap.main(args)
                get.assert_not_called()
                opener.assert_not_called()
            report = json.loads(output.getvalue())
            self.assertEqual(report["segments"], [dict(start_open_at=0, end_close_at=900, candle_count=1)])
            self.assertEqual(report["source"]["sha256"], sha256(body).hexdigest())
            self.assert_research_only(report)
            self.assertEqual(path.read_bytes(), body)
            with patch.object(gap, "open", create=True) as read, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit): gap.main(args[1:])
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
