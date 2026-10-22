"""Synthetic CLI integration only; never connect to Wallex."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import unittest
from unittest.mock import patch
import warnings

from . import credit_preflight as cli
from .test_credit_readonly import FAKE, FakeTransport, setup_fixture
from .credit_readonly import GET_PATHS, HttpReply


def arguments():
    binding, _, _, _ = setup_fixture()
    return ["--connect", "--sub-account-client-id", binding.sub_account_client_id,
            "--owner-user-id", str(binding.owner_user_id), "--package-id", str(binding.package_id),
            "--user-package-id", str(binding.user_package_id), "--currency", binding.currency,
            "--plan-type", binding.plan_type, "--expected-plan-status", "SYNTHETIC_ACTIVE"]


def invoke(argv=None, payloads=None, *, tty=True, prompt=None, now=1800000):
    if payloads is None:
        _, _, payloads, _ = setup_fixture()
    transport = FakeTransport([HttpReply(200, json.dumps(p).encode()) for p in payloads])
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err), \
            patch.object(cli.sys.stdin, "isatty", return_value=tty), \
            patch.object(err, "isatty", return_value=tty), \
            patch.object(cli.getpass, "getpass", side_effect=prompt or (lambda *a, **k: FAKE)) as hidden, \
            patch.object(cli, "UrllibGetTransport", return_value=transport) as factory, \
            patch.object(cli.time, "time", side_effect=[1799990, 1799995, 1799998, now]):
        try:
            code = cli.main(arguments() if argv is None else argv)
        except SystemExit as error:
            code = error.code
    return code, json.loads(out.getvalue()), err.getvalue(), transport, hidden, factory


class CreditPreflightTests(unittest.TestCase):
    def assert_safe(self, report, err):
        text = json.dumps(report) + err
        self.assertNotIn(FAKE, text)
        self.assertNotIn("synthetic-credit-001", text)
        self.assertNotIn("packageDetail", text)
        self.assertEqual(err, "")
        self.assertEqual(report["executable_action"], "NO_TRADE")
        self.assertFalse(report["executable_ready"] or report["credential_scope_verified"])
        self.assertEqual(report["financial_validation"], "NOT_PERFORMED")

    def test_hidden_prompt_three_gets_and_sanitized_summary(self):
        code, report, err, transport, hidden, _ = invoke()
        self.assertEqual(code, 0)
        hidden.assert_called_once()
        self.assertEqual([r.path for r in transport.calls], list(GET_PATHS))
        self.assertTrue(all(r.method == "GET" and r.headers["x-api-key"] == FAKE for r in transport.calls))
        self.assertEqual(report["connection"], "CONNECTED")
        self.assertEqual(report["account_type"], "TRADE_CREDIT")
        self.assertEqual(report["credit_plan_status"], "MATCHED_CALLER_LABEL")
        self.assertEqual(set(report["data_availability"].values()), {"PRESENT"})
        self.assertEqual(report["validation_failures"], [])
        self.assert_safe(report, err)

    def test_identity_status_freshness_and_unexpected_failures_are_sanitized(self):
        for case, expected in (("account", "AMBIGUOUS_SCOPE"), ("package", "AMBIGUOUS_SCOPE"),
                               ("suspended", "INACTIVE_OR_SUSPENDED"), ("type", "PLAN_TYPE_MISMATCH"),
                               ("currency", "CURRENCY_MISMATCH"), ("status", "PLAN_STATUS_MISMATCH"),
                               ("balances", "BALANCE_CURRENCY_MISMATCH"),
                               ("stale", "INVALID_OR_STALE_OBSERVATION"),
                               ("future", "INVALID_OR_STALE_OBSERVATION")):
            _, _, payloads, _ = setup_fixture()
            if case == "account": payloads[0]["result"][0]["clientId"] = "other"
            elif case == "package": payloads[1]["result"]["packageDetail"]["id"] = 999
            elif case == "suspended": payloads[0]["result"][0]["isSuspend"] = True
            elif case == "type": payloads[1]["result"]["userPackageDetail"]["type"] = "other-type"
            elif case == "currency": payloads[1]["result"]["packageDetail"]["currency"] = "IRR"
            elif case == "status": payloads[1]["result"]["userPackageDetail"]["status"] = "UNKNOWN"
            elif case == "balances": payloads[2]["result"]["TMN"]["symbol"] = "IRR"
            now = 1800021 if case == "stale" else 1799989 if case == "future" else 1800000
            with self.subTest(case=case):
                code, report, err, _, _, _ = invoke(payloads=payloads, now=now)
                self.assertEqual(code, 1)
                self.assertIn(expected, report["validation_failures"])
                self.assert_safe(report, err)
        with patch.object(cli, "_identity_failures", side_effect=RuntimeError(FAKE)):
            code, report, err, _, _, _ = invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report["validation_failures"], ["PREFLIGHT_FAILED"])
        self.assert_safe(report, err)

    def test_no_echo_fallback_and_explicit_manual_identity_gate(self):
        def no_hidden_input(*args, **kwargs):
            warnings.warn(FAKE, cli.getpass.GetPassWarning)
            self.fail("Echo fallback must never run")
        cases = [(arguments()[1:], True, None, "INVALID_ARGUMENTS"),
                 ([], True, None, "INVALID_ARGUMENTS"),
                 (arguments() + ["--unknown", FAKE], True, None, "INVALID_ARGUMENTS"),
                 (arguments(), False, None, "INTERACTIVE_TERMINAL_REQUIRED"),
                 (arguments(), True, no_hidden_input, "CREDENTIAL_UNAVAILABLE"),
                 (arguments(), True, lambda *a, **k: "", "CREDENTIAL_UNAVAILABLE")]
        for argv, tty, prompt, expected in cases:
            with self.subTest(expected=expected):
                code, report, err, transport, hidden, factory = invoke(argv, tty=tty, prompt=prompt)
                self.assertNotEqual(code, 0)
                self.assertEqual(report["validation_failures"], [expected])
                self.assertEqual(transport.calls, [])
                if expected != "CREDENTIAL_UNAVAILABLE":
                    hidden.assert_not_called()
                    factory.assert_not_called()
                self.assert_safe(report, err)


if __name__ == "__main__":
    unittest.main()
