"""Synthetic UDF payloads and injected transports only; never contact Wallex."""

from dataclasses import replace
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from . import history as h
from .evaluation import EvidenceKind
from .normalization import Timeframe


def wire(start, end):
    count = (end - start) // 60
    return dict(s="ok", t=list(range(start, end, 60)), o=["10"] * count,
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


if __name__ == "__main__":
    unittest.main()
