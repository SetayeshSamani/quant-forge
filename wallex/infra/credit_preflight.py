"""Manual local GET-only connectivity check; never credit-risk approval."""

import argparse
import getpass
import json
import sys
import time
import warnings

from .credit_account import AccountKind, CreditAccountBinding
from .credit_readonly import CreditReadOnlyClient, CredentialScope, ReadOnlyConfig, UrllibGetTransport


def _summary(connection="NOT_ATTEMPTED", failures=(), matched=False):
    return {
        "connection": connection,
        "account_type": "TRADE_CREDIT" if matched else "UNVERIFIED",
        "credit_plan_status": "MATCHED_CALLER_LABEL" if matched else "UNVERIFIED",
        "data_availability": {name: "PRESENT" if matched else "NOT_CONFIRMED"
                              for name in ("accounts", "plan", "balances")},
        "validation_failures": list(failures),
        "financial_validation": "NOT_PERFORMED",
        "credential_scope_verified": False,
        "executable_action": "NO_TRADE",
        "executable_ready": False,
    }


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        # Never echo unknown arguments, supplied identities or foreign errors.
        print(json.dumps(_summary(failures=("INVALID_ARGUMENTS",))))
        raise SystemExit(2)


def _label(value):
    if not value or len(value) > 200 or not all(33 <= ord(c) <= 126 for c in value):
        raise argparse.ArgumentTypeError("Invalid identifier")
    return value


def _positive(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
        raise argparse.ArgumentTypeError("Invalid positive integer")
    return int(value)


def _arguments(argv):
    parser = _Arguments(description="Manual Trade Credit GET-only preflight; no trading.")
    parser.add_argument("--connect", action="store_true", required=True,
                        help="Opt in and attest the selected credit account has read access.")
    parser.add_argument("--diagnose-scope", action="store_true",
                        help="Read only /sub-accounts and report categorical identity checks.")
    for name in ("sub-account-client-id", "plan-type", "expected-plan-status"):
        parser.add_argument("--" + name, type=_label, required=True)
    for name in ("owner-user-id", "package-id", "user-package-id"):
        parser.add_argument("--" + name, type=_positive, required=True)
    parser.add_argument("--currency", choices=("TMN", "USDT"), required=True)
    return parser.parse_args(argv)


def _prompt_credential(reference):
    # getpass can otherwise fall back to echoed input when hidden input fails.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass("Wallex Trade Credit API key (hidden): ", stream=sys.stderr)


def _identity_failures(bundle, args, now):
    failures = []
    reads = (bundle.accounts, bundle.plan, bundle.balances)
    if (type(now) is not int or any(type(r.observed_at) is not int
            or not 0 <= now - r.observed_at <= 30 for r in reads)):
        failures.append("INVALID_OR_STALE_OBSERVATION")
    rows = bundle.accounts.response().payload["result"]
    selected = next(row for row in rows if row["clientId"] == args.sub_account_client_id)
    if selected["isActive"] is not True or selected["isSuspend"] is not False:
        failures.append("INACTIVE_OR_SUSPENDED")
    plan = bundle.plan.response().payload["result"]
    package, active = plan["packageDetail"], plan["userPackageDetail"]
    if active["type"] != args.plan_type:
        failures.append("PLAN_TYPE_MISMATCH")
    if (package["currency"] != args.currency
            or package["destination_currency"] not in (None, args.currency)):
        failures.append("CURRENCY_MISMATCH")
    if active["status"] != args.expected_plan_status:
        failures.append("PLAN_STATUS_MISMATCH")
    if args.expected_plan_status.casefold() in {
            "inactive", "suspended", "expired", "closed", "liquid", "liquidated", "canceled", "cancelled"}:
        failures.append("INACTIVE_PLAN_LABEL")
    balances = bundle.balances.response().payload["result"]
    if args.currency not in balances or any(row["symbol"] != symbol for symbol, row in balances.items()):
        failures.append("BALANCE_CURRENCY_MISMATCH")
    return tuple(failures)


def main(argv=None):
    args = _arguments(argv)
    try:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            report = _summary(failures=("INTERACTIVE_TERMINAL_REQUIRED",))
        else:
            binding = CreditAccountBinding(
                args.sub_account_client_id, args.owner_user_id, args.package_id,
                args.user_package_id, args.currency, AccountKind.TRADE_CREDIT, args.plan_type,
            )
            config = ReadOnlyConfig(
                credential_reference="local-hidden-prompt",
                credential_sub_account_client_id=binding.sub_account_client_id,
                scope=CredentialScope.TRADE_CREDIT, read_permission_confirmed=True, opt_in=args.connect,
            )
            client = CreditReadOnlyClient(transport=UrllibGetTransport(),
                                        resolve_credential=_prompt_credential,
                                        observation_clock=lambda: int(time.time()))
            result = client.fetch(binding, config=config, diagnose_scope=args.diagnose_scope)
            if args.diagnose_scope and result.scope_checks is not None:
                checks = dict(result.scope_checks)
                failures = []
                if (checks["client_id_match"] != "UNIQUE" or checks["user_id_match"] is not True
                        or checks["account_type"] != "prop" or checks["duplicate_client_ids"]):
                    failures.append("AMBIGUOUS_SCOPE")
                if checks["is_active"] is not True or checks["is_suspended"] is not False:
                    failures.append("INACTIVE_OR_UNVERIFIED_STATUS")
                report = _summary("DIAGNOSTIC_COMPLETE", failures)
                report["data_availability"]["accounts"] = "PRESENT"
                report.update(scope_checks=checks, http_status=result.http_status)
            elif not result.collected:
                report = _summary("FAILED", (result.reason.value,))
                report.update(failed_endpoint=result.failed_endpoint, http_status=result.http_status)
            else:
                failures = _identity_failures(result.bundle, args, int(time.time()))
                report = _summary("FAILED" if failures else "CONNECTED", failures, matched=not failures)
    except (EOFError, KeyboardInterrupt):
        report = _summary(failures=("CANCELLED",))
    except Exception:
        report = _summary("FAILED", ("PREFLIGHT_FAILED",))
    print(json.dumps(report, sort_keys=True))
    return int(report["connection"] not in ("CONNECTED", "DIAGNOSTIC_COMPLETE")
               or bool(report["validation_failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
