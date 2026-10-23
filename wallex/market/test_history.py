"""Synthetic UDF payloads and injected transports only; never contact Wallex."""

from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from wallex.market import history as h
from wallex.market.evaluation import EvidenceKind
from wallex.market.normalization import Timeframe


def wire(start, end, step=60):
    count = (end - start) // step
    return dict(s="ok", t=list(range(start, end, step)), o=["10"] * count,
                h=["12"] * count, l=["9"] * count, c=["11"] * count, v=["0.1"] * count)


def encoded(data):
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


class HistoricalDataTests(unittest.TestCase):
    def test_actual_minutes_aggregate_exactly_and_public_transport_is_get_only(self):
        body = encoded(wire(0, 3600)).replace(b'"0.1"', b'0.1')  # Exact JSON numeric tokens, no float conversion.
        minutes = h.decode_minutes(body, symbol="TESTUSDT", start=0, end=3600)
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            m15 = h.aggregate_minutes(minutes, symbol="TESTUSDT", timeframe=Timeframe.M15, as_of=3600)
            hourly = h.aggregate_minutes(minutes, symbol="TESTUSDT", timeframe=Timeframe.H1, as_of=3600)
        self.assertEqual([c.timestamp for c in m15], [0, 900, 1800, 2700])
        self.assertEqual((m15[0].open, m15[0].high, m15[0].low, m15[0].close, m15[0].volume),
                         (Decimal(10), Decimal(12), Decimal(9), Decimal(11), Decimal("1.5")))
        self.assertEqual(hourly[0].volume, Decimal(6))
        response = Mock(status=200)
        response.read.return_value = body
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = manager
        with patch.object(h, "build_opener", return_value=opener) as build:
            self.assertEqual(h.public_get(h.ENDPOINT + "?symbol=TESTUSDT", timeout_seconds=3), body)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(set(dict(request.header_items())), {"Accept", "Accept-encoding"})
        response.read.assert_called_once_with(h.MAX_BYTES + 1)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(build.call_args.args[1].redirect_request(None))
        self.assertTrue(build.call_args.args[2]._context.check_hostname)

    def test_missing_duplicate_wrong_spacing_incomplete_and_unbounded_requests_fail_closed(self):
        for case in ("wrong-spacing", "missing", "duplicate", "misaligned", "length", "incomplete", "ohlc", "symbol", "status"):
            data = wire(0, 3600)
            if case == "wrong-spacing": data["t"] = list(range(0, 54000, 900))
            elif case == "missing": data["t"].pop()
            elif case == "duplicate": data["t"][1] = 0
            elif case == "misaligned": data["t"] = [t + 1 for t in data["t"]]
            elif case == "length": data["v"].pop()
            elif case == "incomplete": data["completed"] = [False] * 60
            elif case == "ohlc": data["l"][0] = "13"
            elif case == "symbol": data["symbol"] = "OTHERUSDT"
            else: data["s"] = "no_data"
            with self.subTest(case=case), self.assertRaises(ValueError):
                h.decode_minutes(encoded(data), symbol="TESTUSDT", start=0, end=3600)
        with TemporaryDirectory() as directory:
            config = h.HistoryRequest("TESTUSDT", "TEST", "USDT", 0, 3600, Path(directory) / "new", "base")
            transport = Mock(side_effect=AssertionError("Must not fetch"))
            for cfg, fetch in ((config, False), (replace(config, volume_unit="quote"), True),
                               (replace(config, end=15 * 86400), True),
                               (replace(config, end=7200, chunk_hours=1, max_requests=1), True),
                               (replace(config, start=1), True), (replace(config, end=999999999999), True)):
                with self.subTest(config=cfg), self.assertRaises(ValueError):
                    h.collect_history(cfg, fetch=fetch, transport=transport, clock=lambda: 10000000)
            transport.assert_not_called()
            self.assertFalse(config.output.exists())

    def test_collection_preserves_raw_hashes_and_reuses_offline_research_rejecting_tampering(self):
        with TemporaryDirectory() as directory:
            config = h.HistoryRequest("TESTUSDT", "TEST", "USDT", 0, 251 * 3600,
                                      Path(directory) / "history", "base", chunk_hours=10)
            replies = []
            def transport(url, *, timeout_seconds):
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["resolution"], ["1"])
                body = encoded(wire(int(query["from"][0]), int(query["to"][0]) + 1))
                replies.append(body)
                return body
            manifest = h.collect_history(config, fetch=True, transport=transport,
                clock=lambda: config.end + 1, evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
            self.assertEqual(manifest["status"], "VALIDATED")
            self.assertEqual(len(replies), 26)
            self.assertEqual(manifest["datasets"]["m15.jsonl"]["count"], 1004)
            self.assertEqual(manifest["datasets"]["h1.jsonl"]["count"], 251)
            for raw, record in zip(replies, manifest["requests"]):
                self.assertEqual((config.output / record["file"]).read_bytes(), raw)
                self.assertEqual(record["sha256"], sha256(raw).hexdigest())
            report = h.research_history(config.output)
            self.assertEqual(report["evidence_kind"], "synthetic_fixture")
            self.assertEqual((report["steps"], report["candidate_count"]), (5, 0))
            self.assertEqual(report["action"], "NO_TRADE")
            self.assertFalse(report["executable_ready"])
            self.assertEqual(report, h.research_history(config.output))
            metadata = config.output / "manifest.json"
            saved_metadata = metadata.read_bytes()
            wrong_identity = manifest | {"symbol": "OTHERUSDT", "base_asset": "OTHER"}
            metadata.write_text(h.canonical_json(wrong_identity), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SOURCE_REQUEST_IDENTITY"):
                h.research_history(config.output)
            metadata.write_bytes(saved_metadata)
            dataset = config.output / "m15.jsonl"
            original = dataset.read_bytes()
            dataset.write_bytes(original.replace(b'"open":"10"', b'"open":"10.0"', 1))
            with self.assertRaisesRegex(ValueError, "DATASET_HASH"):
                h.research_history(config.output)
            dataset.write_bytes(original)
            (config.output / manifest["requests"][0]["file"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "RAW_SOURCE_HASH"):
                h.research_history(config.output)


    def test_native_h1_is_exact_closed_and_independent_of_decimal_context(self):
        start = 1791590400  # 2026-10-10T00:00:00Z; synthetic payload, not QA data.
        end = start + 264 * 3600
        data = wire(start, end, step=3600)
        data["symbol"] = "TESTUSDT"
        data["completed"] = [True] * 264
        data["o"][0] = "10.12345678901234567890123456789"
        data["v"][0] = "0.12345678901234567890123456789"
        body = encoded(data).replace(b'"0.1"', b'0.1')
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            candles = h.decode_hours(body, symbol="TESTUSDT", start=start, end=end, as_of=end)
        self.assertEqual(len(candles), 264)
        self.assertEqual([c.timestamp for c in candles], list(range(start, end, 3600)))
        self.assertTrue(all(c.timeframe is Timeframe.H1 and c.completed for c in candles))
        self.assertEqual((candles[0].open, candles[0].high, candles[0].low, candles[0].close, candles[0].volume),
                         (Decimal(data["o"][0]), Decimal(12), Decimal(9), Decimal(11), Decimal(data["v"][0])))
        self.assertEqual(candles[-1].timestamp + 3600, end)
        self.assertEqual(candles[-1].volume, Decimal("0.1"))  # Native volume is not multiplied by 60.
        self.assertEqual(candles, h.decode_hours(body, symbol="TESTUSDT", start=start, end=end, as_of=end + 1))

    def test_native_h1_rejects_invalid_coverage_ohlcv_and_cutoffs(self):
        for case in ("missing", "duplicate", "unordered", "misaligned", "minute-spacing", "boolean-time",
                     "float-time", "milliseconds", "before-start", "at-end", "truncated-array", "incomplete",
                     "ohlc", "negative-volume", "nonfinite", "missing-price", "nonpositive", "wrong-symbol", "no-data"):
            data = wire(3600, 4 * 3600, step=3600)
            if case == "missing":
                for key in ("t", "o", "h", "l", "c", "v"): data[key].pop(1)
            elif case == "duplicate": data["t"][1] = data["t"][0]
            elif case == "unordered": data["t"] = list(reversed(data["t"]))
            elif case == "misaligned": data["t"] = [t + 1 for t in data["t"]]
            elif case == "minute-spacing": data["t"] = [3600, 3660, 3720]
            elif case == "boolean-time": data["t"][0] = True
            elif case == "float-time": data["t"][0] = 3600.0
            elif case == "milliseconds": data["t"] = [t * 1000 for t in data["t"]]
            elif case == "before-start": data["t"][0] = 0
            elif case == "at-end": data["t"][-1] = 4 * 3600
            elif case == "truncated-array": data["v"].pop()
            elif case == "incomplete": data["completed"] = [True, False, True]
            elif case == "ohlc": data["l"][0] = "13"
            elif case == "negative-volume": data["v"][0] = "-1"
            elif case == "nonfinite": data["v"][0] = "NaN"
            elif case == "missing-price": data["o"][0] = None
            elif case == "nonpositive": data["c"][0] = "0"
            elif case == "wrong-symbol": data["symbol"] = "OTHERUSDT"
            else: data["s"] = "no_data"
            with self.subTest(case=case), self.assertRaises(ValueError):
                h.decode_hours(encoded(data), symbol="TESTUSDT", start=3600, end=4 * 3600, as_of=4 * 3600)
        body = encoded(wire(0, 7200, step=3600))
        for start, end, as_of in ((0, 7200, 7199), (1, 7200, 7200), (0, 7199, 7200),
                                  (0, 0, 7200), (-3600, 7200, 7200), (False, 7200, 7200),
                                  (0, 7200, None), (0, 7200, 7200.0), (0, 15 * 86400, 15 * 86400)):
            with self.subTest(range=(start, end, as_of)), self.assertRaises(ValueError):
                h.decode_hours(body, symbol="TESTUSDT", start=start, end=end, as_of=as_of)

    def test_native_collection_preserves_hourly_provenance_hashes_and_cli_mode(self):
        with TemporaryDirectory() as directory:
            start = 1791590400
            config = h.HistoryRequest("TESTUSDT", "TEST", "USDT", start, start + 264 * 3600,
                                      Path(directory) / "native", "base", chunk_hours=10)
            replies = []
            def transport(url, *, timeout_seconds):
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["resolution"], ["60"])
                self.assertEqual(query["symbol"], [config.symbol])
                self.assertEqual(timeout_seconds, config.timeout_seconds)
                body = encoded(wire(int(query["from"][0]), int(query["to"][0]) + 1, step=3600))
                replies.append(body)
                return body
            with patch.object(h, "aggregate_minutes", side_effect=AssertionError("No M1 aggregation")):
                manifest = h.collect_history(config, fetch=True, native_h1=True, transport=transport,
                    clock=lambda: config.end + 1, evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
            self.assertEqual((manifest["status"], manifest["ingestion"], manifest["aggregation"]),
                             ("VALIDATED", "NATIVE_H1", "NONE"))
            self.assertEqual((manifest["source_resolution_requested"], manifest["source_spacing_seconds"]), ("60", 3600))
            self.assertEqual((manifest["volume_semantics"], manifest["volume_unit_verification"]),
                             ("CALLER_ATTESTED_BASE", "UNVERIFIED"))
            self.assertEqual((manifest["endpoint"], manifest["evidence_kind"]), (h.ENDPOINT, "synthetic_fixture"))
            self.assertEqual(len(replies), 27)
            self.assertEqual(set(manifest["datasets"]), {"h1.jsonl"})
            self.assertFalse((config.output / "m15.jsonl").exists())
            dataset = h.load_dataset(config.output / "h1.jsonl", symbol=config.symbol, timeframe=Timeframe.H1,
                                     timestamp_unit=h.TimestampUnit.SECONDS, as_of=config.end)
            self.assertEqual(manifest["datasets"]["h1.jsonl"], dict(sha256=dataset.sha256, count=264))
            self.assertEqual(dataset.sha256, sha256((config.output / "h1.jsonl").read_bytes()).hexdigest())
            self.assertEqual((dataset.first_open_at, dataset.last_open_at), (config.start, config.end - 3600))
            self.assertEqual(dataset.candles[-1].volume, Decimal("0.1"))
            for raw, record in zip(replies, manifest["requests"]):
                self.assertEqual((config.output / record["file"]).read_bytes(), raw)
                self.assertEqual((record["sha256"], record["byte_count"], record["observed_at"]),
                                 (sha256(raw).hexdigest(), len(raw), config.end + 1))
            self.assertEqual(json.loads((config.output / "manifest.json").read_text(encoding="utf-8")), manifest)
            with self.assertRaisesRegex(ValueError, "VALIDATED_PUBLIC_HISTORY_REQUIRED"):
                h.research_history(config.output)  # H1-only is not a combined M15/H1 research dataset.
            with patch.object(h, "collect_history", return_value=manifest) as collect, redirect_stdout(StringIO()):
                h.main(["--fetch", "--native-h1", "--symbol", "TESTUSDT", "--base", "TEST", "--quote", "USDT",
                        "--start", "2026-10-10T00:00Z", "--end", "2026-10-21T00:00Z", "--volume-unit", "base",
                        "--output", str(Path(directory) / "cli")])
            self.assertTrue(collect.call_args.kwargs["native_h1"])
            self.assertTrue(collect.call_args.kwargs["fetch"])
            self.assertEqual((collect.call_args.args[0].start, collect.call_args.args[0].end), (config.start, config.end))

    def test_native_collection_guards_and_partial_failures_never_fallback_or_validate(self):
        with TemporaryDirectory() as directory:
            config = h.HistoryRequest("TESTUSDT", "TEST", "USDT", 0, 7200,
                                      Path(directory) / "guards", "base", chunk_hours=1)
            transport = Mock(side_effect=AssertionError("Must not fetch"))
            for cfg, options in ((config, {"fetch": False, "native_h1": True}),
                                 (config, {"fetch": True, "native_h1": "60"}),
                                 (replace(config, volume_unit=None), {"fetch": True, "native_h1": True}),
                                 (replace(config, volume_unit="quote"), {"fetch": True, "native_h1": True}),
                                 (replace(config, end=10800), {"fetch": True, "native_h1": True}),
                                 (replace(config, max_requests=1), {"fetch": True, "native_h1": True})):
                with self.subTest(guard=(cfg, options)), self.assertRaises(ValueError):
                    h.collect_history(cfg, **options, transport=transport, clock=lambda: 7200)
            transport.assert_not_called()
            self.assertFalse(config.output.exists())
            with patch.object(h, "collect_history") as collect, redirect_stdout(StringIO()), patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit):
                    h.main(["--research", "--native-h1", "--input", str(config.output)])
                collect.assert_not_called()
            for failure in ("gap", "receipt-regressed", "network"):
                cfg = replace(config, output=Path(directory) / failure)
                calls = []
                clock = iter((7210, 7210, 7209 if failure == "receipt-regressed" else 7210))
                def failing_transport(url, *, timeout_seconds):
                    calls.append(parse_qs(urlsplit(url).query))
                    if len(calls) == 2 and failure == "network":
                        raise OSError("Synthetic public transport failure")
                    data = wire((len(calls) - 1) * 3600, len(calls) * 3600, step=3600)
                    if len(calls) == 2 and failure == "gap":
                        for key in ("t", "o", "h", "l", "c", "v"): data[key].clear()
                    return encoded(data)
                with self.subTest(failure=failure), self.assertRaises((ValueError, OSError)):
                    h.collect_history(cfg, fetch=True, native_h1=True, transport=failing_transport,
                        clock=lambda: next(clock), evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
                manifest = json.loads((cfg.output / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["status"], "INCOMPLETE")
                self.assertEqual(manifest["datasets"], {})
                self.assertFalse((cfg.output / "h1.jsonl").exists())
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(query["resolution"] == ["60"] for query in calls))
                self.assertEqual(len(manifest["requests"]), 2 if failure == "gap" else 1)
                for record in manifest["requests"]:
                    self.assertEqual(sha256((cfg.output / record["file"]).read_bytes()).hexdigest(), record["sha256"])

if __name__ == "__main__":
    unittest.main()
