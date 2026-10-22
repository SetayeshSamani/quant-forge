"""Research reporting only; existing synthetic replay and strategy reference data."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wallex.market.datasets import load_dataset
from wallex.market.evaluation import EvidenceKind, canonical_json
from wallex.market.normalization import Timeframe, TimestampUnit
from wallex.market.replay import ReplayPlan
from wallex.strategy.test_frame_adapter import AS_OF, frame_fixture
from .research import evaluate_candidates
from .tp1 import run_tp1_shadow
from .tp1_demo import run_demo


def crossover_plan():
    # Reuse the hand-checked strategy fixture; write its actual bytes for hashes.
    frame = frame_fixture(m15_count=22, h1_count=251)
    datasets = []
    with TemporaryDirectory() as directory:
        for timeframe, bars in ((Timeframe.M15, frame.candles_15m), (Timeframe.H1, frame.candles_1h)):
            path = Path(directory) / (timeframe.name + ".jsonl")
            fields = ("timestamp", "open", "high", "low", "close", "volume", "completed")
            path.write_text("".join(canonical_json({key: getattr(bar, key) for key in fields}) + "\n"
                                    for bar in bars), encoding="utf-8")
            datasets.append(load_dataset(path, symbol=frame.symbol, timeframe=timeframe,
                                         as_of=AS_OF, timestamp_unit=TimestampUnit.SECONDS))
    return ReplayPlan(*datasets, frame.base_asset, frame.quote_asset, AS_OF - 900, AS_OF)


class CandidateResearchTests(unittest.TestCase):
    def test_existing_demo_has_no_candidates_and_reports_actual_technical_failures(self):
        demo = run_demo()
        report = demo["research"]
        self.assertEqual((report["steps"], report["candidate_count"], report["rejected_step_count"]), (13, 0, 13))
        self.assertEqual(report["candidate_timestamps"], [])
        self.assertEqual(report["technical_rejection_reason_counts"], {
            "trend:EMA20_NOT_ABOVE_EMA50": 13, "trend:EMA20_NOT_RISING_THREE_BARS": 13,
            "pullback:CURRENT_CLOSE_NOT_ABOVE_EMA20": 13, "pullback:DISTANCE_NOT_POSITIVE": 13,
        })
        self.assertEqual(report["evidence_kind"], "synthetic_fixture")
        self.assertEqual(report["window"], [900000, 910800])
        self.assertEqual(report["action"], "NO_TRADE")
        self.assertFalse(report["executable_ready"])
        self.assertTrue(all(step["action"] == "NO_TRADE" for step in demo["steps"]))

    def test_actual_crossover_is_causal_and_stop_needs_explicit_pe_not_approval(self):
        plan = crossover_plan()
        options = dict(strategy_version="tp1-candidate-reference-v1", evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
        for pe in (None, Decimal(110)):
            with self.subTest(pe_supplied=pe is not None):
                shadow = run_tp1_shadow(plan, pe=pe, **options)
                self.assertEqual(shadow.summary.error_count, 0)
                report = evaluate_candidates(shadow)
                self.assertEqual(report, evaluate_candidates(shadow))
                self.assertEqual(report["candidate_count"], 1)
                self.assertEqual(report["candidate_timestamps"], [AS_OF])
                self.assertEqual(report["rejected_step_count"], 1)
                candidate = report["candidates"][0]
                evidence = candidate["evidence"]
                self.assertTrue(evidence["trend"]["eligible"] and evidence["pullback"]["eligible"])
                self.assertEqual(evidence["trend"]["latest_closed_at"], AS_OF)
                self.assertEqual(evidence["pullback"]["decision_closed_at"], AS_OF)
                self.assertFalse(candidate["execution_approved"] or candidate["executable_ready"])
                self.assertEqual(candidate["action"], "NO_TRADE")
                self.assertTrue(all(record.decision.quantity_base is None for record in shadow.evaluations))
                if pe is None:
                    self.assertIsNone(candidate["stop_valid"])
                    self.assertIsNone(evidence["stop"])
                    self.assertIn("PE_UNAVAILABLE", candidate["frame_reasons"])
                else:
                    self.assertTrue(candidate["stop_valid"])
                    self.assertEqual(evidence["stop"]["entry_price"], "110")
                    self.assertNotEqual(evidence["stop"]["entry_price"], evidence["pullback"]["current_close"])
                    self.assertEqual(evidence["stop"]["initial_stop"], "97.6")
        earlier = run_tp1_shadow(replace(plan, end_as_of=AS_OF - 900), **options)
        self.assertEqual(earlier.summary.error_count, 0)
        self.assertEqual(evaluate_candidates(earlier)["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
