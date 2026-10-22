"""End-to-end synthetic demo checks, with all network entry points forbidden."""

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from io import StringIO
import json
import unittest
from unittest.mock import patch

from wallex.infra.test_credit_account import inputs as credit_fixture
from . import tp1_demo as demo


class TP1DemoTests(unittest.TestCase):
    def test_runnable_demo_is_deterministic_synthetic_and_never_executable(self):
        outputs = []
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            for _ in range(2):
                output = StringIO()
                with redirect_stdout(output):
                    demo.main()
                outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        report = json.loads(outputs[0])
        summary = report["summary"]
        self.assertEqual(report["evidence_kind"], "synthetic_fixture")
        self.assertEqual((summary["steps"], summary["errors"], summary["no_trade"],
                          summary["blocked"], summary["credit_evidence_ready"]), (13, 0, 13, 13, 13))
        self.assertFalse(summary["executable_ready"])
        self.assertEqual(report["synthetic_assumptions"]["equity"], "10000")
        self.assertEqual(report["synthetic_assumptions"]["free_quote"], "2000")
        self.assertEqual([step["as_of"] for step in report["steps"]], list(range(900000, 910801, 900)))
        for step in report["steps"]:
            self.assertEqual(step["action"], "NO_TRADE")
            self.assertEqual(step["evidence_kind"], "synthetic_fixture")
            self.assertEqual(step["risk_credit"]["context_reasons"], [])
            self.assertEqual(step["risk_credit"]["input_reasons"], [])
            self.assertEqual(step["risk_credit"]["credit_reasons"], ["LOCAL_EVIDENCE_VALID"])
            self.assertIn("APPROVED_RISK_UNAVAILABLE", step["blocking_reasons"])
            self.assertTrue(step["risk_credit"]["blocks_new_entries"])
            self.assertFalse(step["risk_credit"]["executable_ready"])
        for forbidden in ("quantity", "x-api-key", "userId", "clientId", "packageDetail", "synthetic-credit-001"):
            self.assertNotIn(forbidden, outputs[0])
        template = credit_fixture()
        frame = next(demo.synthetic_plan().steps()).frame
        risk = demo.synthetic_risk_inputs(frame, template.binding)
        self.assertEqual(risk.equity, Decimal("10000"))
        self.assertNotEqual(risk.equity, Decimal(template.balances.payload["result"]["TMN"]["available"]))
        self.assertEqual(risk.loss_state.current_equity, risk.equity)
        self.assertEqual(risk.portfolio_state.snapshot_observed_at, frame.as_of - 2)

    def test_missing_stale_and_inconsistent_synthetic_evidence_stays_blocked(self):
        original = demo.synthetic_credit_inputs
        for case, expected in (("missing", "MISSING_EVIDENCE"), ("stale", "STALE_EVIDENCE"),
                               ("account", "IDENTITY_MISMATCH"), ("balance", "INCONSISTENT_BALANCES"),
                               ("cutoff", "CREDIT_CUTOFF_MISMATCH"), ("currency", "CREDIT_QUOTE_MISMATCH")):
            def broken(frame, template):
                evidence = original(frame, template)
                if case == "missing": return replace(evidence, plan=None)
                if case == "stale":
                    return replace(evidence, plan=replace(evidence.plan, observed_at=frame.as_of - 31))
                if case == "account":
                    return replace(evidence, accounts=replace(evidence.accounts, sub_account_client_id="other-synthetic"))
                if case == "balance":
                    payload = deepcopy(evidence.balances.payload)
                    payload["result"]["TMN"]["total"] = "1"
                    return replace(evidence, balances=replace(evidence.balances, payload=payload))
                if case == "cutoff": return replace(evidence, as_of=frame.as_of + 1)
                return replace(evidence, binding=replace(evidence.binding, currency="USDT"))
            with self.subTest(case=case), patch.object(demo, "synthetic_credit_inputs", side_effect=broken):
                report = demo.run_demo()
                self.assertEqual(report["summary"]["credit_evidence_ready"], 0)
                self.assertEqual(report["summary"]["blocked"], 13)
                self.assertEqual(report["summary"]["no_trade"], 13)
                self.assertEqual(report["summary"]["errors"], 0)
                for step in report["steps"]:
                    self.assertIn(expected, step["blocking_reasons"])
                    self.assertTrue(step["risk_credit"]["blocks_new_entries"])
                    self.assertFalse(step["risk_credit"]["executable_ready"])


if __name__ == "__main__":
    unittest.main()
