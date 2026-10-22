"""Actual TP-1 assessments over existing synthetic replay/strategy fixtures."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from wallex.backtest.example import example_plan
from wallex.market.evaluation import Action, EvidenceKind
from wallex.strategy.frame_adapter import assess_market_frame
from wallex.strategy.test_frame_adapter import frame_fixture
from wallex.infra.test_credit_account import inputs as credit_fixture
from wallex.risk.test_pipeline import fixture as risk_fixture
from wallex.risk.pipeline import assess_risk_pipeline
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


    def test_per_step_risk_is_assessed_and_journaled_without_credit_approval(self):
        supplied = {}
        def risk_for_step(frame):
            risk = risk_fixture()
            risk = replace(risk, frame=frame,
                loss_state=replace(risk.loss_state, as_of=frame.as_of,
                                   day_start_at=frame.as_of // 86400 * 86400,
                                   equity_observed_at=frame.as_of),
                portfolio_state=replace(risk.portfolio_state, as_of=frame.as_of,
                                        snapshot_observed_at=frame.as_of))
            supplied[frame.as_of] = risk
            return risk
        options = dict(strategy_version="tp1-risk-diagnostics-v1",
                       evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE,
                       risk_inputs_for_step=risk_for_step)
        with patch("wallex.risk.credit_pipeline.assess_risk_pipeline", wraps=assess_risk_pipeline) as assess:
            result = tp1.run_tp1_shadow(example_plan(), **options)
        self.assertEqual(assess.call_count, 13)
        self.assertEqual(result.summary.error_count, 0)
        events = [event for event in result.events if event.kind == "evaluation"]
        for record, event in zip(result.evaluations, events):
            detail = json.loads(record.decision.diagnostics_json)
            expected = assess_risk_pipeline(supplied[record.as_of])
            self.assertEqual(detail["risk_reasons"], [r.value for r in expected.reasons])
            for name in ("loss", "portfolio", "sizing"):
                component = getattr(expected, name)
                self.assertEqual(detail[name + "_reasons"],
                                 None if component is None else [r.value for r in component.reasons])
            self.assertEqual(expected.frame.metadata.as_of, record.as_of)
            self.assertIn("CREDIT_EVIDENCE_MISSING", detail["input_reasons"])
            self.assertFalse(detail["credit_evidence_ready"])
            self.assertTrue(detail["blocks_new_entries"])
            self.assertIs(record.decision.action, Action.NO_TRADE)
            self.assertIsNone(record.decision.quantity_base)
            self.assertEqual(event.payload["decision"]["diagnostics_json"], record.decision.diagnostics_json)
            self.assertNotIn("synthetic-account", record.decision.diagnostics_json)
            self.assertNotIn("theoretical_quantity", record.decision.diagnostics_json)
        self.assertEqual(result, tp1.run_tp1_shadow(example_plan(), **options))

    def test_step_mismatches_missing_evidence_and_local_success_stay_blocked(self):
        credit, risk, frame = credit_fixture(), risk_fixture(), frame_fixture()
        frame.quote_asset = credit.binding.currency  # Explicit synthetic currency, no conversion.
        account = credit.binding.sub_account_client_id
        risk = replace(risk, frame=frame, account_id=account, quote_asset=frame.quote_asset,
                       loss_state=replace(risk.loss_state, quote_asset=frame.quote_asset),
                       portfolio_state=replace(risk.portfolio_state, account_id=account,
                                               quote_asset=frame.quote_asset))
        cases = ((risk, credit, None, None),
                 (None, credit, None, "RISK_INPUTS_MISSING"),
                 (risk, None, None, "CREDIT_EVIDENCE_MISSING"),
                 (risk, replace(credit, plan=None), None, "MISSING_EVIDENCE"),
                 (replace(risk, frame=deepcopy(frame)), credit, None, "RISK_FRAME_MISMATCH"),
                 (replace(risk, account_id="other"), credit, None, "CREDIT_ACCOUNT_MISMATCH"),
                 (replace(risk, loss_state=replace(risk.loss_state, as_of=frame.as_of + 1)),
                  credit, None, "CUTOFF_MISMATCH"),
                 (risk, replace(credit, as_of=frame.as_of + 1), None, "CREDIT_CUTOFF_MISMATCH"),
                 (risk, replace(credit, binding=replace(credit.binding, currency="USDT")),
                  None, "CREDIT_QUOTE_MISMATCH"),
                 (replace(risk, worst_case_extra_loss_per_unit=None), credit, None, "MISSING_COST_EVIDENCE"),
                 (risk, credit, Decimal(111), "PE_MISMATCH"))
        for step_risk, step_credit, pe, expected in cases:
            with self.subTest(expected=expected):
                decision = tp1.tp1_decision(frame, pe=pe,
                    risk_inputs_for_step=lambda f: step_risk, credit_inputs_for_step=lambda f: step_credit)
                detail = json.loads(decision.diagnostics_json)
                self.assertIs(decision.action, Action.NO_TRADE)
                self.assertIsNone(decision.quantity_base)
                self.assertTrue(detail["blocks_new_entries"])
                self.assertFalse(detail["executable_ready"])
                if expected is None:
                    self.assertTrue(detail["credit_evidence_ready"])
                    self.assertTrue(detail["risk_local_conditions_satisfied"])
                    self.assertTrue(json.loads(decision.reason)["technical_eligible"])
                else:
                    codes = [code for key, values in detail.items() if key.endswith("reasons")
                             for code in (values or [])]
                    self.assertIn(expected, codes)
                if detail["context_reasons"]:
                    self.assertFalse(detail["risk_local_conditions_satisfied"])
                    self.assertIsNone(detail["sizing_reasons"])
        def failing_provider(frame):
            raise RuntimeError("private provider detail")
        decision = tp1.tp1_decision(frame, risk_inputs_for_step=failing_provider)
        self.assertIn("RISK_INPUTS_PROVIDER_FAILED", json.loads(decision.diagnostics_json)["input_reasons"])
        self.assertNotIn("private provider detail", decision.diagnostics_json)
        from wallex.market.evaluation import ReplayDecision
        for invalid in ("[]", "x" * 16385):
            with self.assertRaises(ValueError):
                ReplayDecision(Action.NO_TRADE, "diagnostic", diagnostics_json=invalid)


if __name__ == "__main__":
    unittest.main()
