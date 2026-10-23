"""Synthetic supervised-observer checks; no real private/public HTTP in tests."""

from contextlib import redirect_stdout
from dataclasses import replace
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from wallex.audit.ledger import Ledger, StorageError
from wallex.audit.writer import WriterBusy
from wallex.market.evaluation import EvidenceKind
from wallex.market.test_history import wire, encoded
from wallex.market.test_public_orderbook import fixture as book_fixture, encoded as book_encoded
from . import agent_runtime as agent, credit_preflight
from .test_credit_preflight import arguments
from .test_credit_readonly import FakeTransport, FAKE, setup_fixture
from .credit_readonly import HttpReply

AS_OF = 1800000


def config(directory):
    return agent.ObserverConfig(Path(directory) / "observer", "BTCTMN", "BTC", "TMN", "base")


def sources(case=None):
    calls = []
    def history(url, **options):
        calls.append(url)
        query = parse_qs(urlsplit(url).query)
        step = 60 if query["resolution"] == ["1"] else 3600
        start, end = int(query["from"][0]), int(query["to"][0]) + 1
        if case == "network":
            raise TimeoutError("must never appear in journal")
        data = wire(start, end, step)
        if (case == "h1-gap" and step == 3600) or (case == "m1-gap" and step == 60):
            for key in ("t", "o", "h", "l", "c", "v"):
                data[key].pop(len(data[key]) // 2)
        return encoded(data)
    def depth(url, **options):
        calls.append(url)
        return (403, b"private message") if case == "book-http" else (200, book_encoded(book_fixture()))
    return history, depth, calls


def run(cfg, **options):
    historical, book, calls = sources()
    defaults = dict(fetch_public=True, once=True, clock=lambda: AS_OF, history_transport=historical,
                    book_transport=book, emit=lambda record: None, evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
    defaults.update(options)
    return agent.run_observer(cfg, **defaults), calls


class AgentRuntimeTests(unittest.TestCase):
    def test_actual_composition_audits_three_public_reads_without_order_intents(self):
        with TemporaryDirectory() as directory:
            cfg = config(directory)
            report, calls = run(cfg)
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(url.startswith("https://api.wallex.ir/v1/") for url in calls))
            self.assertEqual(report["status"], "OBSERVED_BLOCKED")
            self.assertEqual(report["decision_cutoff"], AS_OF)
            self.assertEqual(report["pipeline"]["validation"]["h1_candles"], 264)
            self.assertEqual(report["pipeline"]["validation"]["m1_m15"]["complete_m15"], 24)
            summary = report["pipeline"]["summary"]
            self.assertEqual((summary["evaluated_steps"], summary["technical_eligible_steps"], summary["replay_errors"]), (4, 0, 0))
            self.assertEqual(report["account_evidence"], "UNVERIFIED_NOT_FETCHED")
            self.assertEqual(report["action"], "NO_TRADE")
            self.assertFalse(report["executable_ready"])
            self.assertIn("REPLAY_GATEWAY_LIVE_UNAVAILABLE", report["readiness_blockers"])
            saved = next((cfg.state_dir / "observations").iterdir())
            for name in ("m1", "h1"):
                self.assertEqual(report["requests"][name]["raw_sha256"], sha256((saved / (name + ".json")).read_bytes()).hexdigest())
            ledger = Ledger(cfg.state_dir / "audit.sqlite")
            self.assertEqual(ledger.records(agent.PARTITION), ())
            self.assertEqual(ledger.events()[-1].kind, "observer_stopped")
            self.assertEqual(ledger.events()[-1].payload["status"], "STOPPED")
            self.assertTrue(all(event.intent_id is None for event in ledger.events()))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.main(["status", "--state-dir", str(cfg.state_dir)]), 0)
            status = json.loads(output.getvalue())
            self.assertEqual(status["last_lifecycle"]["status"], "STOPPED")
            self.assertIsNone(status["last_preflight"])
            self.assertFalse(status["executable_ready"])

    def test_gaps_network_book_and_cutoff_failures_remain_degraded_and_unexecutable(self):
        for case in ("h1-gap", "m1-gap", "network", "book-http", "cutoff"):
            with self.subTest(case=case), TemporaryDirectory() as directory:
                cfg = config(directory)
                historical, book, calls = sources(case)
                ticks = iter([AS_OF] * 7 + [AS_OF + 900])
                report = agent.observe_cycle(cfg, Path(directory) / "cycle", fetch_public=True,
                                             history_transport=historical, book_transport=book,
                                             clock=(lambda: next(ticks)) if case == "cutoff" else (lambda: AS_OF),
                                             evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
                self.assertEqual(report["health"], "DEGRADED")
                self.assertFalse(report["executable_ready"])
                self.assertEqual(report["action"], "NO_TRADE")
                self.assertEqual(len(calls), 3)
                self.assertNotIn("must never appear", agent.canonical_json(report))
                self.assertNotIn("private message", agent.canonical_json(report))
                if case == "m1-gap":
                    self.assertEqual(report["pipeline"]["summary"]["evaluated_steps"], 0)
                    self.assertEqual(report["pipeline"]["validation"]["m1_m15"]["segments"], 2)
                elif case == "book-http":
                    self.assertEqual(report["orderbook"]["http_status"], 403)
                else:
                    self.assertTrue(report["observation_failures"])
                    self.assertIsNone(report["pipeline"])

    def test_restart_dedup_binding_rollback_and_controlled_continuous_shutdown(self):
        with TemporaryDirectory() as directory:
            cfg = config(directory)
            run(cfg)
            historical, book = Mock(), Mock()
            report, _ = run(cfg, history_transport=historical, book_transport=book)
            self.assertEqual(report["status"], "ALREADY_OBSERVED_NO_HTTP")
            historical.assert_not_called()
            book.assert_not_called()
            with self.assertRaisesRegex(ValueError, "STATE_BINDING_MISMATCH"):
                run(replace(cfg, symbol="BTCUSDT", quote_asset="USDT"))
            with self.assertRaisesRegex(ValueError, "LOCAL_CLOCK_BEHIND_JOURNAL"):
                run(cfg, clock=lambda: AS_OF - 900)
            stop = threading.Event()
            emitted = []
            def emit(record):
                emitted.append(record)
                stop.set()
            run(cfg, once=False, max_cycles=10, clock=lambda: AS_OF + 900, stop=stop, emit=emit)
            self.assertEqual(len(emitted), 1)
            self.assertEqual(Ledger(cfg.state_dir / "audit.sqlite").events()[-1].kind, "observer_stopped")
            # An intentional stop must not signal failure to Restart=on-failure.
            def stopped_worker(*args, **options):
                options["stop"].set()
                return agent._safe("OBSERVATION_DEGRADED")
            with patch.object(agent, "run_observer", side_effect=stopped_worker), \
                 patch.object(agent.signal, "signal"):
                self.assertEqual(agent.main(["observe", "--fetch-public", "--state-dir", str(cfg.state_dir),
                                             "--symbol", "BTCTMN", "--base", "BTC", "--quote", "TMN"]), 0)

    def test_existing_interactive_preflight_preserves_401_and_never_persists_secrets(self):
        _, _, payloads, _ = setup_fixture()
        transport = FakeTransport([HttpReply(200, json.dumps(payloads[0]).encode()), HttpReply(401, b"private")])
        with TemporaryDirectory() as directory:
            cfg = config(directory)
            output = StringIO()
            with patch.object(credit_preflight, "UrllibGetTransport", return_value=transport), \
                 patch.object(credit_preflight.sys.stdin, "isatty", return_value=True), \
                 patch.object(credit_preflight.sys.stderr, "isatty", return_value=True), \
                 patch.object(credit_preflight.getpass, "getpass", return_value=FAKE), \
                 patch.object(agent, "_now", return_value=AS_OF), redirect_stdout(output):
                code = agent.preflight(cfg, arguments() + ["--diagnose-prop"])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(report["http_status"], 401)
            self.assertFalse(report["credential_scope_verified"] or report["executable_ready"])
            self.assertEqual(report["action"], "NO_TRADE")
            events = Ledger(cfg.state_dir / "audit.sqlite").events()
            text = json.dumps([event.payload for event in events]) + output.getvalue()
            for forbidden in (FAKE, "synthetic-credit-001", "private", "x-api-key"):
                self.assertNotIn(forbidden, text)
            self.assertTrue(all(request.method == "GET" for request in transport.calls))
            self.assertEqual([request.path for request in transport.calls], ["/sub-accounts", "/v1/prop/user/packages/active"])

    def test_optin_storage_and_single_writer_fail_before_any_network(self):
        with TemporaryDirectory() as directory:
            cfg = config(directory)
            transport = Mock()
            with self.assertRaisesRegex(ValueError, "EXPLICIT_PUBLIC_FETCH_REQUIRED"):
                agent.run_observer(cfg, history_transport=transport)
            self.assertFalse(cfg.state_dir.exists())
            with patch.object(Ledger, "record_event", side_effect=StorageError("disk unavailable")):
                with self.assertRaises(StorageError):
                    run(cfg, history_transport=transport, book_transport=transport)
            transport.assert_not_called()
            ledger = Ledger(cfg.state_dir / "audit.sqlite")
            with ledger.writer(agent.PARTITION), self.assertRaises(WriterBusy):
                run(cfg, history_transport=transport, book_transport=transport)
            transport.assert_not_called()
