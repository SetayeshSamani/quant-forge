"""Synthetic public-depth responses and injected transports; no Wallex calls."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from . import public_orderbook as book


def fixture():
    return {"success": True, "result": {
        "bid": [{"price": "99", "quantity": "0.1", "sum": "9.9"},
                {"price": "98", "quantity": "0.1", "sum": "9.8"}],
        "ask": [{"price": "101", "quantity": "0.1", "sum": "10.1"},
                {"price": "102", "quantity": "0.1", "sum": "10.2"}]}}


def encoded(data):
    # Exercise the documented JSON numeric quantity, with no float round-trip.
    return json.dumps(data, separators=(",", ":")).encode().replace(b'"quantity":"0.1"', b'"quantity":0.1')


def analyze(payload, **options):
    return book.analyze_orderbook(payload, symbol="BTCTMN", received_at=100, **options)


class PublicOrderbookTests(unittest.TestCase):
    def assert_disabled(self, result):
        self.assertEqual(result["action"], "NO_TRADE")
        self.assertFalse(result["executable_ready"])
        self.assertIsNone(result["source_event_time"])
        self.assertEqual(result["verified_freshness"], "UNAVAILABLE")
        self.assertEqual(result["market_identity"], "UNVERIFIED")
        self.assertNotIn("quantity", result)

    def test_documented_wire_spread_hash_and_context_independent_precision(self):
        payload = encoded(fixture())
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            result = analyze(payload)
        self.assertTrue(result["valid"])
        self.assertEqual((result["best_bid"], result["best_ask"], result["midpoint"], result["spread_pct"]),
                         (Decimal(99), Decimal(101), Decimal(100), Decimal(2)))
        self.assertEqual((result["bid_levels"], result["ask_levels"]), (2, 2))
        self.assertEqual(result["raw_sha256"], sha256(payload).hexdigest())
        self.assertEqual(result["received_at"], 100)
        self.assertIn("SOURCE_EVENT_TIME_UNAVAILABLE", result["reasons"])
        self.assert_disabled(result)
        self.assertEqual(result, analyze(payload))
        data = fixture()
        data["result"]["bid"] = [dict(data["result"]["bid"][0], price="9999999999999999999999999999999999")]
        data["result"]["ask"] = [dict(data["result"]["ask"][0], price="10000000000000000000000000000000001")]
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            precise = analyze(encoded(data))
        self.assertTrue(precise["valid"])
        self.assertEqual(precise["midpoint"], Decimal("1e34"))
        self.assertEqual(precise["spread_pct"], Decimal("2e-32"))
        self.assert_disabled(precise)

    def test_bad_shapes_levels_ordering_numbers_and_limits_fail_closed(self):
        variants = []
        for side in ("bid", "ask"):
            for case in ("reversed", "duplicate", "empty", "non-list", "missing-field"):
                data = deepcopy(fixture())
                levels = data["result"][side]
                if case == "reversed": levels.reverse()
                elif case == "duplicate": levels[1]["price"] = levels[0]["price"]
                elif case == "empty": levels.clear()
                elif case == "non-list": data["result"][side] = {}
                else: del levels[0]["sum"]
                variants.append((side + case, encoded(data)))
        for key in ("price", "quantity", "sum"):
            for value in ("0", "-1", "NaN", "Infinity", True, None, "1e101", "1e" + "9" * 150):
                data = fixture()
                data["result"]["bid"][0][key] = value
                variants.append((key + str(value), encoded(data)))
        for ask in ("98", "99"):
            data = fixture()
            data["result"]["ask"][0]["price"] = ask
            variants.append(("crossed/locked", encoded(data)))
        variants.extend([(name, value) for name, value in (
            ("malformed", b"{"), ("nonfinite-json", b'{"success":NaN}'),
            ("duplicate-key", b'{"success":true,"success":false}'), ("utf8", b"\xff"),
            ("shape", b"[]"), ("false-success", b'{"success":false,"message":"private body"}'),
            ("oversized", b" " * (book.MAX_BYTES + 1)))])
        for case, payload in variants:
            with self.subTest(case=case):
                result = analyze(payload)
                self.assertFalse(result["valid"])
                self.assertIsNone(result["best_bid"])
                self.assertIsNone(result["spread_pct"])
                self.assertNotIn("private body", book.canonical_json(result))
                self.assert_disabled(result)
        result = analyze(encoded(fixture()), max_levels=1)
        self.assertIn("LEVEL_LIMIT_EXCEEDED:bid", result["reasons"])
        self.assert_disabled(result)
        result = book.analyze_orderbook(encoded(fixture()), symbol="BTCTMN", received_at=True)
        self.assertIn("INVALID_LOCAL_RECEIPT_TIME", result["reasons"])

    def test_no_http_without_optin_and_transport_has_fixed_origin_get_tls_and_bounds(self):
        transport = Mock(return_value=(200, encoded(fixture())))
        clock = Mock(return_value=100)
        result = book.fetch_orderbook(symbol="BTCTMN", transport=transport, clock=clock)
        self.assertIn("EXPLICIT_FETCH_REQUIRED", result["reasons"])
        transport.assert_not_called()
        clock.assert_not_called()
        self.assert_disabled(result)
        for options in ({"symbol": "BTCTMN&private=1"}, {"timeout_seconds": 0}, {"timeout_seconds": 11},
                        {"timeout_seconds": True}, {"max_levels": 0}, {"max_levels": 1001}):
            with self.subTest(options=options):
                result = book.fetch_orderbook(**({"symbol": "BTCTMN"} | options), fetch=True, transport=transport)
                self.assertFalse(result["valid"])
                self.assert_disabled(result)
        transport.assert_not_called()
        response = Mock(status=200)
        response.read.return_value = encoded(fixture())
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = manager
        with patch.object(book, "build_opener", return_value=opener) as build:
            result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, timeout_seconds=3, clock=iter((100, 101)).__next__)
            for url in ("https://example.com/v1/depth?symbol=BTCTMN", book.ENDPOINT + "?symbol=BTCTMN&extra=1"):
                with self.assertRaises(book.BookError):
                    book._public_get(url, timeout_seconds=3)
        self.assertTrue(result["valid"])
        self.assertEqual((result["request_started_at"], result["received_at"], result["http_status"]), (100, 101, 200))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, book.ENDPOINT + "?symbol=BTCTMN")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(set(dict(request.header_items())), {"Accept", "Accept-encoding"})
        opener.open.assert_called_once()
        response.read.assert_called_once_with(book.MAX_BYTES + 1)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(build.call_args.args[1].redirect_request(None))
        self.assertTrue(build.call_args.args[2]._context.check_hostname)
        self.assert_disabled(result)

    def test_http_network_malformed_transport_and_clock_failures_do_not_retry_or_leak(self):
        for status in (301, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                transport = Mock(return_value=(status, b"private error body"))
                result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, transport=transport, clock=lambda: 100)
                self.assertIn("HTTP_" + str(status), result["reasons"])
                self.assertNotIn("private error body", book.canonical_json(result))
                self.assertEqual(result["http_status"], status)
                transport.assert_called_once_with(book.ENDPOINT + "?symbol=BTCTMN", timeout_seconds=5)
                self.assert_disabled(result)
        for response in ((200, b"{"), (200, b" " * (book.MAX_BYTES + 1)), None):
            result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, transport=lambda *a, **k: response, clock=lambda: 100)
            self.assertFalse(result["valid"])
            self.assert_disabled(result)
        for error, reason in ((TimeoutError("private"), "NETWORK_TIMEOUT"),
                              (URLError(TimeoutError("private")), "NETWORK_TIMEOUT"),
                              (URLError("private"), "NETWORK_ERROR")):
            transport = Mock(side_effect=error)
            result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, transport=transport)
            self.assertIn(reason, result["reasons"])
            self.assertNotIn("private", book.canonical_json(result))
            transport.assert_called_once()
            self.assert_disabled(result)
        result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, transport=lambda *a, **k: (200, encoded(fixture())),
                                      clock=iter((101, 100)).__next__)
        self.assertIn("LOCAL_CLOCK_REVERSED", result["reasons"])
        error_body = Mock()
        error = HTTPError(book.ENDPOINT, 403, "private", {}, error_body)
        with patch.object(book, "build_opener") as build:
            build.return_value.open.side_effect = error
            result = book.fetch_orderbook(symbol="BTCTMN", fetch=True, clock=lambda: 100)
        self.assertIn("HTTP_403", result["reasons"])
        error_body.read.assert_not_called()
        error_body.close.assert_called_once()

    def test_offline_cli_never_fetches_and_outputs_json_for_valid_or_malformed_files(self):
        with TemporaryDirectory() as directory, patch.object(book, "_public_get") as get:
            path = Path(directory) / "synthetic-book.json"
            payload = encoded(fixture())
            path.write_bytes(payload)
            arguments = ["--analyze", "--input", str(path), "--symbol", "BTCTMN", "--received-at", "100"]
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(book.main(arguments), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["spread_pct"], "2")
            self.assertEqual(path.read_bytes(), payload)
            self.assert_disabled(result)
            path.write_bytes(b"{")
            with redirect_stdout(StringIO()):
                self.assertEqual(book.main(arguments), 1)
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                book.main([])
            get.assert_not_called()
