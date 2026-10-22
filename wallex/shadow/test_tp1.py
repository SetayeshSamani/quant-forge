"""Actual TP-1 assessments over existing synthetic replay/strategy fixtures."""

from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from wallex.backtest.example import example_plan
from wallex.market.evaluation import Action, EvidenceKind
from wallex.strategy.frame_adapter import assess_market_frame
from wallex.strategy.test_frame_adapter import frame_fixture
from . import tp1


class TP1ShadowTests(unittest.TestCase):
    def test_existing_replay_journals_actual_reasons_deterministically(self):
        plan = example_plan()
        options = dict(strategy_version="tp1-technical-diagnostics-v1",
                       evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE, pe=Decimal(110))
        with patch.object(tp1, "assess_market_frame", wraps=assess_market_frame) as assess:
            result = tp1.run_tp1_shadow(plan, **options)
        self.assertEqual(assess.call_count, result.summary.step_count)
        self.assertEqual((result.summary.step_count, result.summary.decision_count,
                          result.summary.error_count), (13, 13, 0))
        self.assertEqual(result, tp1.run_tp1_shadow(plan, **options))
        events = [event for event in result.events if event.kind == "evaluation"]
        for step, record, event in zip(plan.steps(), result.evaluations, events):
            expected = assess_market_frame(step.frame, pe=options["pe"])
            reason = json.loads(record.decision.reason)
            self.assertEqual(record.as_of, step.as_of)
            self.assertEqual(reason["reasons"]["frame"], [r.value for r in expected.reasons])
            for name in ("trend", "pullback", "stop"):
                component = getattr(expected, name)
                self.assertEqual(reason["reasons"][name],
                                 None if component is None else [r.value for r in component.reasons])
            self.assertEqual(reason["technical_eligible"], expected.technical_eligible)
            self.assertEqual(reason["risk_approval"], "UNAVAILABLE")
            self.assertFalse(reason["executable_ready"])
            self.assertIs(record.decision.action, Action.NO_TRADE)
            self.assertIsNone(record.decision.quantity_base)
            self.assertEqual(record.frame_quality, "INCOMPLETE")
            self.assertEqual(event.payload["decision"]["reason"], record.decision.reason)
            self.assertEqual(event.evidence_kind, "synthetic_fixture")

    def test_missing_inputs_and_technical_success_never_become_orders(self):
        for frame, pe, expected in ((None, None, "INVALID_FRAME"),
                                    (frame_fixture(), None, "PE_UNAVAILABLE"),
                                    (frame_fixture(), Decimal("NaN"), "PE_INVALID"),
                                    (frame_fixture(), Decimal(110), "PE_PROVISIONAL")):
            with self.subTest(expected=expected):
                decision = tp1.tp1_decision(frame, pe=pe)
                reason = json.loads(decision.reason)
                self.assertIn(expected, reason["reasons"]["frame"])
                self.assertIs(decision.action, Action.NO_TRADE)
                self.assertIsNone(decision.quantity_base)
                self.assertFalse(reason["executable_ready"])
                self.assertEqual(reason["risk_approval"], "UNAVAILABLE")
                if frame is not None:
                    self.assertTrue(reason["technical_eligible"])
                    self.assertIn("QUOTE_UNAVAILABLE", reason["reasons"]["frame"])
                    self.assertIn("DEPTH_UNAVAILABLE", reason["reasons"]["frame"])
                    if expected != "PE_PROVISIONAL":
                        self.assertIsNone(reason["reasons"]["stop"])
                    else:
                        self.assertEqual(reason["reasons"]["stop"], ["LEVELS_VALID"])


if __name__ == "__main__":
    unittest.main()
