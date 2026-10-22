"""Offline composition only; reuse existing synthetic component fixtures."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest

from wallex.infra.credit_account import CreditReason, assess_credit_account
from wallex.infra.test_credit_account import inputs as credit_fixture
from wallex.strategy.engine import Signal
from .credit_pipeline import assess_credit_risk_diagnostics
from .pipeline import PipelineReason, assess_risk_pipeline
from .test_pipeline import fixture as risk_fixture


class CreditRiskDiagnosticsTests(unittest.TestCase):
    def test_missing_plan_blocks_even_when_independent_risk_checks_pass(self):
        credit, risk = credit_fixture(), risk_fixture()
        expected_risk = assess_risk_pipeline(risk)
        for evidence, reason in ((None, CreditReason.INVALID_INPUT),
                                 (replace(credit, plan=None), CreditReason.MISSING_EVIDENCE),
                                 (replace(credit, plan=replace(credit.plan, complete=False)),
                                  CreditReason.INCOMPLETE_EVIDENCE)):
            with self.subTest(reason=reason):
                result = assess_credit_risk_diagnostics(evidence, risk)
                self.assertEqual(result.credit, assess_credit_account(evidence))
                self.assertIn(reason, result.credit.reasons)
                self.assertFalse(result.credit_evidence_ready)
                self.assertIsNone(result.credit.snapshot)
                self.assertEqual(result.risk, expected_risk)
                self.assertTrue(result.risk.local_conditions_satisfied)
                self.assertTrue(result.blocks_new_entries)
                self.assertFalse(result.executable_ready)
                self.assertIs(result.executable_action, Signal.NO_TRADE)
                if evidence is not None:
                    self.assertEqual(result.credit.rejected_field, "plan" if evidence.plan is None else "plan.complete")

    def test_complete_credit_never_fills_risk_inputs_or_authorizes_execution(self):
        credit, risk = credit_fixture(), risk_fixture()
        for inputs in (risk, replace(risk, equity=None, free_quote=None)):
            with self.subTest(missing_equity=inputs.equity is None):
                before = deepcopy((credit, inputs))
                result = assess_credit_risk_diagnostics(credit, inputs)
                self.assertTrue(result.credit_evidence_ready)
                self.assertIsNone(result.credit.snapshot.risk_equity)
                self.assertEqual(result.risk, assess_risk_pipeline(inputs))
                self.assertTrue(result.blocks_new_entries)
                self.assertFalse(result.executable_ready)
                self.assertIs(result.executable_action, Signal.NO_TRADE)
                self.assertIn("CREDIT_RISK_CORRELATION_UNVERIFIED", result.limitations)
                self.assertEqual((credit, inputs), before)
                self.assertEqual(result, assess_credit_risk_diagnostics(credit, inputs))
                if inputs.equity is None:
                    self.assertIn(PipelineReason.MISSING_SIZING_INPUT, result.risk.reasons)
                    self.assertIsNone(result.risk.sizing)
                else:
                    self.assertTrue(result.risk.local_conditions_satisfied)
                    self.assertEqual(result.risk.sizing.inputs.equity, inputs.equity)
                with self.assertRaises(FrozenInstanceError):
                    result.executable_ready = True
                with self.assertRaises((TypeError, ValueError)):
                    replace(result, executable_ready=True)


if __name__ == "__main__":
    unittest.main()
