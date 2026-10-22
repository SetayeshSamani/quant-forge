"""Read-only decision logging, cross-mode comparison, and complete offline CLI flow."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import subprocess
import sys
import unittest

from wallex.backtest import SimulationConfig, run_backtest
from wallex.backtest.example import example_plan, synthetic_decision
from wallex.market.evaluation import Action, EvidenceKind, ReplayDecision
from . import compare_runs, run_shadow


class ShadowTests(unittest.TestCase):
    def run_case(self, provider=synthetic_decision, **changes):
        options = dict(strategy_version="fixture-v1", evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
        return run_shadow(example_plan(), provider, **(options | changes))

    def test_observes_buys_and_sells_without_creating_fills_positions_or_receipts(self):
        result = self.run_case()
        self.assertEqual((result.summary.step_count, result.summary.decision_count,
                          result.summary.error_count), (13, 13, 0))
        self.assertEqual(result.evaluations[0].decision.action, Action.BUY)
        self.assertEqual(result.evaluations[4].decision.action, Action.SELL)
        self.assertEqual(set(e.kind for e in result.events), {"run_started", "evaluation", "run_completed"})
        self.assertFalse(hasattr(result, "fills"))
        for event in result.events:
            self.assertEqual(event.mode, "shadow")
            self.assertEqual(event.evidence_kind, "synthetic_fixture")
            self.assertEqual(event.payload["mode"], "shadow")
            self.assertEqual(event.payload["evidence_kind"], "synthetic_fixture")
        self.assertTrue(all(e.frame_quality == "INCOMPLETE" for e in result.evaluations))

    def test_every_no_trade_and_error_is_logged_and_processing_continues(self):
        def provider(frame):
            if frame.as_of == 900900:
                raise ValueError("fixture error")
            if frame.as_of == 901800:
                return {"action": "BUY"}
            return ReplayDecision(Action.NO_TRADE, "explicit idle reason")
        result = self.run_case(provider)
        self.assertEqual(result.summary.error_count, 2)
        self.assertEqual(result.evaluations[1].error_code, "PROVIDER_ERROR")
        self.assertEqual(result.evaluations[2].error_code, "INVALID_DECISION")
        self.assertEqual(result.evaluations[-1].decision.reason, "explicit idle reason")
        self.assertEqual(sum(e.kind == "evaluation" for e in result.events), 13)

    def test_warmup_errors_are_recorded_without_fabricating_decisions(self):
        plan = example_plan()
        plan = replace(plan, start_as_of=plan.m15.candles[0].timestamp + 900)
        seen = []
        def provider(frame):
            seen.append(frame.as_of)
            return ReplayDecision(Action.NO_TRADE, "fixture")
        result = run_shadow(plan, provider, strategy_version="fixture-v1",
                            evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
        self.assertGreater(result.summary.error_count, 0)
        self.assertEqual(len(seen), 13)
        self.assertIsNone(result.evaluations[0].decision)
        self.assertTrue(result.evaluations[0].error_code.startswith("DATA_"))

    def test_strategy_version_and_evidence_kind_must_be_explicit_valid_values(self):
        for changes in ({"strategy_version": ""}, {"strategy_version": None},
                        {"evidence_kind": "synthetic_fixture"}, {"evidence_kind": "live"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_case(**changes)

    def test_events_and_export_are_repeatable_and_payload_reads_are_copies(self):
        result = self.run_case()
        self.assertEqual(result, self.run_case())
        payload = result.events[0].payload
        payload["mode"] = "live"
        self.assertEqual(result.events[0].payload["mode"], "shadow")
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "shadow"
            result.export(destination)
            names = {p.name for p in destination.iterdir()}
            self.assertEqual(names, {"run.json", "summary.json", "events.jsonl", "evaluations.jsonl"})
            self.assertNotIn("fills.jsonl", names)

    def test_comparison_matches_decisions_while_modes_and_simulation_costs_differ(self):
        shadow = self.run_case()
        backtest = run_backtest(example_plan(), synthetic_decision,
                                config=SimulationConfig(Decimal(1000), Decimal("0.1"), Decimal("0.2"), 900),
                                strategy_version="fixture-v1", evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
        comparison = compare_runs(backtest, shadow)
        self.assertTrue(comparison.matches)
        self.assertEqual(comparison.compared_steps, 13)
        self.assertNotEqual(backtest.run_id, shadow.run_id)

    def test_comparison_pinpoints_decision_drift(self):
        def changed(frame):
            if frame.as_of == 904500:
                return ReplayDecision(Action.NO_TRADE, "changed reason")
            return synthetic_decision(frame)
        comparison = compare_runs(self.run_case(), self.run_case(changed))
        self.assertFalse(comparison.matches)
        self.assertEqual([d.as_of for d in comparison.differences], [904500])

    def test_comparison_rejects_unrelated_provenance_and_duplicate_timestamps(self):
        first = self.run_case()
        with self.assertRaises(ValueError):
            compare_runs(first, self.run_case(strategy_version="different-v2"))
        duplicated = replace(first, evaluations=first.evaluations + (first.evaluations[0],))
        with self.assertRaises(ValueError):
            compare_runs(first, duplicated)

    def test_full_offline_flow_works_with_network_entry_points_forbidden(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                shadow = self.run_case()
                backtest = run_backtest(example_plan(), synthetic_decision,
                                        config=SimulationConfig(Decimal(1000), Decimal(0), Decimal(0), 0),
                                        strategy_version="fixture-v1",
                                        evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
        self.assertTrue(compare_runs(backtest, shadow).matches)

    def test_cli_exports_complete_artifacts_from_an_unrelated_working_directory(self):
        root = Path(__file__).resolve().parents[2]
        script = """
import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
sys.argv[0] = 'wallex.backtest.example'
runpy.run_module('wallex.backtest.example', run_name='__main__')
assert not any(name.startswith(('wallex.execution', 'wallex.strategy', 'wallex.risk', 'wallex.audit'))
               for name in sys.modules)
"""
        with TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts"
            completed = subprocess.run(
                [sys.executable, "-B", "-c", script, str(root), "--output", str(output)],
                cwd=directory, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertEqual(summary["backtest"]["net_pnl_quote"], "-0.6")
            self.assertTrue(summary["comparison"]["matches"])
            self.assertTrue((output / "backtest" / "equity.jsonl").is_file())
            self.assertTrue((output / "shadow" / "events.jsonl").is_file())
            self.assertTrue(json.loads((output / "comparison.json").read_text())["matches"])


if __name__ == "__main__":
    unittest.main()
