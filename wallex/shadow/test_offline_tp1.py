"""Focused synthetic offline integration checks; no public/private API calls."""

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wallex.market.evaluation import EvidenceKind, canonical_json
from wallex.market.test_gap_aware import encoded, wire
from wallex.strategy.test_frame_adapter import frame_fixture
from . import offline_tp1 as offline


def synthetic_files(directory):
    # Reuse the independently checked technical fixture. These independent
    # synthetic histories do not claim cross-timeframe price equivalence.
    frame = frame_fixture()
    minutes = wire([stamp for bar in frame.candles_15m
                    for stamp in range(bar.timestamp, bar.timestamp + 900, 60)])
    for key, field in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
        minutes[key] = [str(getattr(bar, field)) for bar in frame.candles_15m for _ in range(15)]
    hours = wire([bar.timestamp for bar in frame.candles_1h])
    for key, field in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
        hours[key] = [str(getattr(bar, field)) for bar in frame.candles_1h]
    m1, h1 = Path(directory) / "m1.json", Path(directory) / "h1.json"
    m1.write_bytes(encoded(minutes))
    h1.write_bytes(encoded(hours))
    options = dict(symbol="SYNTHETICUSDT", base_asset="SYNTHETIC", quote_asset="USDT",
                   m1_start=minutes["t"][0], m1_end=frame.as_of,
                   h1_start=hours["t"][0], h1_end=frame.as_of, as_of=frame.as_of,
                   evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE, volume_unit="base")
    return m1, h1, options


class OfflineTP1Tests(unittest.TestCase):
    def test_two_nineteen_bar_segments_never_manufacture_a_warmup(self):
        with TemporaryDirectory() as directory:
            m1, h1, options = synthetic_files(directory)
            start, end = 1791828000, 1791864000
            missing = (1791845940, 1791846000, 1791846060)
            m1.write_bytes(encoded(wire([stamp for stamp in range(start, end, 60) if stamp not in missing])))
            h1_start = start - 250 * 3600
            h1.write_bytes(encoded(wire(range(h1_start, end, 3600))))
            options.update(m1_start=start, m1_end=end, h1_start=h1_start, h1_end=end, as_of=end)
            with patch.object(offline, "run_tp1_shadow") as shadow:
                report = offline.run_offline(m1, h1, **options)
            shadow.assert_not_called()
            self.assertEqual([row["candle_count"] for row in report["segments"]], [19, 19])
            self.assertTrue(all(not row["m15_warmup_sufficient"] for row in report["segments"]))
            self.assertEqual(report["requirements"]["consecutive_m15"], 21)
            self.assertFalse(report["requirements"]["segments_may_be_combined"])
            self.assertEqual(report["summary"]["insufficient_data_steps"], 38)
            self.assertEqual(report["summary"]["evaluated_steps"], 0)
            self.assertEqual(report["blocking_reason_counts"], {"INSUFFICIENT_CONSECUTIVE_M15_HISTORY": 38})
            self.assertEqual(report["provenance"]["m1"]["sha256"], sha256(m1.read_bytes()).hexdigest())
            self.assertEqual(report["validation"]["missing_m1_timestamps"], list(missing))
            self.assertEqual(report["action"], "NO_TRADE")
            self.assertFalse(report["executable_ready"])

    def test_real_shadow_diagnostics_are_causal_deterministic_and_permanently_blocked(self):
        with TemporaryDirectory() as directory:
            m1, h1, options = synthetic_files(directory)
            before = m1.read_bytes(), h1.read_bytes()
            with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                report = offline.run_offline(m1, h1, **options)
                self.assertEqual(report, offline.run_offline(m1, h1, **options))
            self.assertEqual(report["summary"]["evaluated_steps"], 1)
            self.assertEqual(report["summary"]["technical_eligible_steps"], 1)
            self.assertEqual(report["summary"]["insufficient_data_steps"], 20)
            step = report["evaluated_steps"][0]
            self.assertEqual((step["m15_history"], step["h1_history"]), (21, 250))
            self.assertEqual(step["technical_evidence"]["trend"]["latest_closed_at"], options["as_of"])
            self.assertEqual(step["technical_evidence"]["pullback"]["decision_closed_at"], options["as_of"])
            self.assertIn("RISK_INPUTS_MISSING", step["input_reasons"])
            self.assertIn("CREDIT_EVIDENCE_MISSING", step["input_reasons"])
            self.assertIn("PE_UNAVAILABLE", step["technical_reasons"]["frame"])
            self.assertIn("QUOTE_UNAVAILABLE", step["technical_reasons"]["frame"])
            self.assertIsNone(step["technical_evidence"]["stop"])
            self.assertIsNone(step["sizing_reasons"])
            self.assertTrue(step["risk_reasons"] and step["credit_reasons"])
            self.assertTrue(all(value == "UNVERIFIED" for value in report["unverified"].values()))
            self.assertEqual(step["action"], "NO_TRADE")
            self.assertFalse(step["executable_ready"])
            self.assertNotIn("quantity", canonical_json(report))
            self.assertEqual(before, (m1.read_bytes(), h1.read_bytes()))
            # A closed H1 bar available at a later file cutoff must not enter the
            # earlier decision, although the source-file digest correctly changes.
            hours = json.loads(h1.read_bytes())
            hours["t"].append(options["as_of"])
            for key in ("o", "h", "l", "c", "v"):
                hours[key].append("999999")
            h1.write_bytes(encoded(hours))
            options.update(h1_end=options["as_of"] + 3600, as_of=options["as_of"] + 3600)
            later = offline.run_offline(m1, h1, **options)["evaluated_steps"][0]
            self.assertEqual(later["h1_history"], 250)
            self.assertEqual(later["technical_evidence"], step["technical_evidence"])
            self.assertEqual(later["blockers"], step["blockers"])

    def test_unknown_units_short_stale_or_malformed_h1_cannot_reach_technical_evaluation(self):
        for case in ("units", "short", "stale", "gap", "future", "identity"):
            with self.subTest(case=case), TemporaryDirectory() as directory:
                m1, h1, options = synthetic_files(directory)
                hours = json.loads(h1.read_bytes())
                if case == "units":
                    options["volume_unit"] = None
                elif case in ("short", "gap"):
                    index = 0 if case == "short" else 120
                    for key in ("t", "o", "h", "l", "c", "v"):
                        hours[key].pop(index)
                    if case == "short":
                        options["h1_start"] += 3600
                elif case == "stale":
                    hours["t"] = [stamp - 3600 for stamp in hours["t"]]
                    options["h1_start"] -= 3600
                    options["h1_end"] -= 3600
                elif case == "future":
                    options["h1_end"] += 3600
                else:
                    hours["symbol"] = "WRONGUSDT"
                h1.write_bytes(encoded(hours))
                with patch.object(offline, "run_tp1_shadow") as shadow:
                    if case in ("gap", "future", "identity"):
                        with self.assertRaises(ValueError):
                            offline.run_offline(m1, h1, **options)
                    else:
                        report = offline.run_offline(m1, h1, **options)
                        self.assertEqual(report["summary"]["evaluated_steps"], 0)
                        expected = {"units": "VOLUME_UNIT_UNVERIFIED",
                                    "short": "INSUFFICIENT_CONSECUTIVE_H1_HISTORY",
                                    "stale": "frame:STALE_HISTORY:candles_1h_dataset"}[case]
                        self.assertIn(expected, report["blocking_reason_counts"])
                        self.assertFalse(report["executable_ready"])
                shadow.assert_not_called()

    def test_cli_reads_only_explicit_local_inputs_and_emits_machine_readable_failures(self):
        with TemporaryDirectory() as directory:
            m1, h1, options = synthetic_files(directory)
            arguments = ["--analyze", "--m1-input", str(m1), "--h1-input", str(h1)]
            for key, flag in (("symbol", "symbol"), ("base_asset", "base"), ("quote_asset", "quote"),
                              ("m1_start", "m1-start"), ("m1_end", "m1-end"),
                              ("h1_start", "h1-start"), ("h1_end", "h1-end"), ("as_of", "as-of")):
                arguments.extend(["--" + flag, str(options[key])])
            arguments.extend(["--evidence-kind", "synthetic_fixture", "--volume-unit", "base"])
            with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(offline.main(arguments), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["evidence_kind"], "synthetic_fixture")
                self.assertEqual(report["summary"]["evaluated_steps"], 1)
                h1.write_text("invalid", encoding="utf-8")
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(offline.main(arguments), 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], "INPUT_REJECTED")
                self.assertEqual(report["action"], "NO_TRADE")
                self.assertFalse(report["executable_ready"])
