"""Independent scoped spot READ preflight: no package IDs, writes or activation."""

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
import json
import re
import ssl
import sys
import time
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from wallex.market.datasets import _unique_object
from wallex.market.evaluation import canonical_json
from wallex.market.history import _NoRedirect, _reject_constant
from .credit_preflight import _prompt_credential
from .credit_account import user_id_format
from .credit_readonly import (
    HttpGet, ReadOnlyConfig, ReadReason, UrllibGetTransport, _scope_checks, _contains,
)

MARKETS_URL = "https://api.wallex.ir/hector/web/v1/markets"
MAX_BYTES = 262144
BASE_BLOCKERS = (
    "TRADE_PERMISSION_NOT_VERIFIED_BY_READS", "HACKATHON_MODE_OWNER_ATTESTED_ONLY",
    "RESPONSE_ACCOUNT_SCOPE_NOT_INDEPENDENTLY_ECHOED", "ACTUAL_FEES_UNVERIFIED",
    "FULL_ORDER_LIMITS_AND_CREDIT_MARKET_ELIGIBILITY_UNVERIFIED",
    "STRATEGY_RISK_APPROVAL_AND_ACCOUNT_RECONCILIATION_REQUIRED",
    "LIVE_LEDGER_ADMISSION_EXIT_AND_FILL_RECOVERY_UNIMPLEMENTED",
    "LOCAL_REAL_MONEY_ACTIVATION_NOT_APPROVED",
)


@dataclass(frozen=True)
class SpotAccountConfig:
    sub_account_client_id: str = field(repr=False)
    owner_user_id: int = field(repr=False)
    symbol: str
    base_asset: str
    quote_asset: str
    hackathon_key_confirmed: bool = False


class SpotGetTransport(UrllibGetTransport):
    """Reuse TLS/secret-safe GET transport; add only bounded documented orders."""
    def _allowed_headers(self, request):
        return (request.path == "/sub-accounts" and set(request.headers) == {"x-api-key"}) or super()._allowed_headers(request)

    def _allowed_path(self, path):
        return path in ("/sub-accounts", "/v1/account/balances-detail") or (
            type(path) is str and re.fullmatch(r"/v1/account/openOrders\?page=[1-3]&per_page=100", path) is not None)


def _report():
    return {"mode": "HACKATHON_SPOT_READ_PREFLIGHT", "authenticated_reads_succeeded": False,
            "discovery_identity_matched": False, "account_type": "UNVERIFIED",
            "balances": "UNAVAILABLE", "quote_available_positive": None,
            "base_exposure_present": None, "other_asset_exposure_present": None,
            "open_orders_observed": None, "order_coverage": "UNVERIFIED", "order_pagination_diagnostics": [],
            "market": "UNVERIFIED", "fees": "UNVERIFIED", "trade_permission": "UNVERIFIED",
            "observed_at": None, "endpoints": {}, "blockers": list(BASE_BLOCKERS),
            "action": "NO_TRADE", "executable_ready": False}


def _number(value):
    if type(value) is not str or len(value) > 200 or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value) is None:
        raise ValueError("INVALID_BALANCE_OR_ORDER_NUMBER")
    number = Decimal(value)
    if not number.is_finite() or number < 0 or len(number.as_tuple().digits) > 100 or abs(number.as_tuple().exponent) > 100:
        raise ValueError("INVALID_BALANCE_OR_ORDER_NUMBER")
    return number


def _market_get():
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(Request(MARKETS_URL, headers={"Accept": "application/json", "Accept-Encoding": "identity"}, method="GET"), timeout=5) as response:
        if response.status != 200:
            raise ValueError("MARKET_HTTP_FAILURE")
        payload = response.read(1048577)
    if len(payload) > 1048576:
        raise ValueError("MARKET_RESPONSE_TOO_LARGE")
    return json.loads(payload.decode("utf-8"), parse_float=Decimal, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def check_account(config=None, *, connect=False, transport=None, resolve_credential=None,
                  select_account=None, symbol="BTCTMN", base_asset="BTC", quote_asset="TMN",
                  market_get=None, clock=lambda: time.time_ns() // 1000000000):
    """Only request-scoped read evidence; no main-account/package fallback.

    Raw account responses/amounts/order IDs remain ephemeral and never returned.
    Discovery does not prove which principal the key belongs to; the dedicated
    key attestation and documented header are not independent response-scope proof.
    """
    report = _report()
    try:
        if connect is not True:
            raise ValueError("EXPLICIT_READ_CONNECT_REQUIRED")
        if config is not None and (type(config) is not SpotAccountConfig or config.hackathon_key_confirmed is not True
                or type(config.owner_user_id) is not int or config.owner_user_id < 1
                or type(config.sub_account_client_id) is not str or not 1 <= len(config.sub_account_client_id) <= 200
                or not all(33 <= ord(c) <= 126 for c in config.sub_account_client_id)
                or any(type(v) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", v) is None
                       for v in (config.symbol, config.base_asset, config.quote_asset))
                or config.symbol != config.base_asset + config.quote_asset or config.base_asset == config.quote_asset):
            raise ValueError("INVALID_EXPLICIT_ACCOUNT_BINDING_OR_ATTESTATION")
        started = clock()
        if type(started) is not int or started < 0:
            raise ValueError("INVALID_LOCAL_CLOCK")
        secret = resolve_credential("local-hidden-spot-prompt") if resolve_credential else None
        if type(secret) is not str or not 1 <= len(secret) <= 4096 or not all(33 <= ord(c) <= 126 for c in secret):
            raise ValueError("CREDENTIAL_UNAVAILABLE")
        transport = transport or SpotGetTransport()
        headers = {"x-api-key": secret}
        if config is not None:
            headers["sub-account-client-id"] = config.sub_account_client_id
        def read(path):
            reply = transport.get(HttpGet(path, dict(headers), 5, MAX_BYTES))
            endpoint = path.split("?")[0]
            report["endpoints"][endpoint] = reply.status if type(reply.status) is int and 100 <= reply.status <= 599 else None
            if type(reply.status) is not int or reply.status != 200:
                status = str(reply.status) if type(reply.status) is int and 100 <= reply.status <= 599 else "INVALID"
                raise ValueError("SCOPED_HTTP_" + status)
            if type(reply.body) is not bytes or len(reply.body) > MAX_BYTES:
                raise ValueError("PRIVATE_RESPONSE_TOO_LARGE_OR_INVALID")
            payload = json.loads(reply.body.decode("utf-8"), parse_float=Decimal,
                                 parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            if _contains(payload, secret) or type(payload) is not dict or payload.get("success") is not True:
                raise ValueError("INVALID_OR_SENSITIVE_RESPONSE")
            return payload
        discovery = read("/sub-accounts")
        if config is None:
            options = _discovered_accounts(discovery, symbol, base_asset, quote_asset)
            if select_account is None:
                raise ValueError("EXPLICIT_ACCOUNT_SELECTION_REQUIRED")
            selected = select_account(options)
            if type(selected) is not SpotAccountConfig or selected not in options:
                raise ValueError("ACCOUNT_SELECTION_NOT_CONFIRMED")
            config = selected
            headers["sub-account-client-id"] = config.sub_account_client_id
            # Human confirmation can take time. Re-read and bind the discovery
            # before collecting balances; never trust a stale menu as admission.
            started = clock()
            if type(started) is not int or started < 0:
                raise ValueError("INVALID_LOCAL_CLOCK")
            discovery = read("/sub-accounts")
        checks = _scope_checks(discovery, config, ReadOnlyConfig("hidden", max_accounts=100))
        report["scope_checks"] = dict(checks)
        if checks["client_id_match"] != "UNIQUE" or checks["duplicate_client_ids"]:
            raise ValueError("ACCOUNT_IDENTITY_NOT_UNIQUE")
        if checks["user_id_match"] is not True:
            raise ValueError("OWNER_IDENTITY_MISMATCH")
        if checks["account_type"] != "prop":
            raise ValueError("ACCOUNT_TYPE_SCOPE_NOT_DOCUMENTED_FOR_THIS_PREFLIGHT")
        if checks["is_active"] is not True or checks["is_suspended"] is not False:
            raise ValueError("ACCOUNT_INACTIVE_OR_SUSPENDED")
        report.update(discovery_identity_matched=True, account_type="prop")
        balances_ok = orders_ok = False
        try:
            rows = read("/v1/account/balances-detail")["result"]
            if type(rows) is not dict or not 1 <= len(rows) <= 300 or not {config.base_asset, config.quote_asset} <= rows.keys():
                raise ValueError("INCOMPLETE_BALANCE_CURRENCIES")
            parsed = {}
            for asset, row in rows.items():
                if type(row) is not dict or row.get("symbol") != asset:
                    raise ValueError("BALANCE_CURRENCY_MISMATCH")
                total, frozen, available = (_number(row[key]) for key in ("total", "freeze", "available"))
                if Fraction(frozen) + Fraction(available) != Fraction(total):
                    raise ValueError("INCONSISTENT_BALANCES")
                parsed[asset] = (total, available)
            report.update(balances="VALID_REQUEST_SCOPED_NOT_EQUITY", quote_available_positive=parsed[config.quote_asset][1] > 0,
                          base_exposure_present=parsed[config.base_asset][0] > 0,
                          other_asset_exposure_present=any(total > 0 for asset, (total, available) in parsed.items() if asset not in (config.base_asset, config.quote_asset)))
            balances_ok = True
            if not report["quote_available_positive"]:
                report["blockers"].append("NO_AVAILABLE_QUOTE_CAPITAL")
            if report["base_exposure_present"] or report["other_asset_exposure_present"]:
                report["blockers"].append("EXPOSURE_RECONCILIATION_REQUIRED")
        except Exception as error:
            report["blockers"].append("balances:" + _reason(error))
        try:
            observed, ids = 0, set()
            report["blockers"].append("ORDER_COVERAGE_UNVERIFIED")
            for page in range(1, 4):
                data = read("/v1/account/openOrders?" + urlencode({"page": page, "per_page": 100}))
                grouped = data["result"]
                if type(grouped) is not dict or any(type(v) is not list for v in grouped.values()):
                    raise ValueError("INVALID_OPEN_ORDER_SHAPE")
                rows = [row for group in grouped.values() for row in group]
                if len(rows) > 100:
                    raise ValueError("ORDER_PAGE_LIMIT_EXCEEDED")
                for row in rows:
                    identity = row.get("clientOrderId") if type(row) is dict else None
                    if (type(identity) is not str or not identity or len(identity) > 200 or identity in ids
                            or row.get("active") is not True or row.get("side") not in ("BUY", "SELL")
                            or type(row.get("symbol")) is not str or type(row.get("status")) is not str):
                        raise ValueError("MALFORMED_DUPLICATE_OR_UNKNOWN_ORDER_EVIDENCE")
                    ids.add(identity)
                    if _number(row["executedQty"]) > _number(row["origQty"]) or _number(row["origQty"]) <= 0:
                        raise ValueError("INCONSISTENT_ORDER_QUANTITY")
                observed += len(rows)
                report["open_orders_observed"] = observed
                # Official Spot result_info is untyped, unlike credit discovery.
                # Inspect its shape only; never invent pagination field semantics
                # or turn an empty page into proof of complete account coverage.
                info = data.get("result_info")
                report["order_pagination_diagnostics"].append({
                    "requested_page": page,
                    "result_info_format": "ABSENT" if "result_info" not in data else _format(info),
                    "field_formats": {key: _format(info[key]) if type(info) is dict and key in info else "ABSENT"
                                      for key in ("page", "count", "total_count", "per_page")},
                })
                report["order_coverage"] = "UNVERIFIED_PROVIDER_PAGINATION"
                if not rows:
                    break  # Bounded stopping rule only, never a flatness claim.
            else:
                report["blockers"].append("ORDER_PAGINATION_BUDGET_EXHAUSTED")
            orders_ok = True  # Valid authenticated reads, not complete coverage.
            if observed:
                report["blockers"].append("OPEN_OR_UNKNOWN_ORDERS_PRESENT")
        except Exception as error:
            report["blockers"].append("orders:" + _reason(error))
        try:
            markets = (market_get or _market_get)()
            rows = markets["result"]["markets"]
            if markets.get("success") is not True or type(rows) is not list or len(rows) > 3000:
                raise ValueError("INVALID_MARKET_RESPONSE")
            matches = [row for row in rows if row.get("symbol") == config.symbol]
            if len(matches) != 1 or matches[0].get("base_asset") != config.base_asset or matches[0].get("quote_asset") != config.quote_asset:
                raise ValueError("MARKET_IDENTITY_NOT_UNIQUE")
            market = matches[0]
            if market.get("is_spot") is not True or any(type(market.get(key)) is not int or not 0 <= market[key] <= 18 for key in ("amount_precision", "price_precision")):
                raise ValueError("MARKET_DISABLED_OR_PRECISION_UNVERIFIED")
            report["market"] = {"symbol": config.symbol, "spot_listed": True, "amount_precision": market["amount_precision"],
                                "price_precision": market["price_precision"], "credit_eligibility": "UNVERIFIED", "full_order_limits": "UNVERIFIED"}
        except Exception as error:
            report["blockers"].append("market:" + _reason(error))
        observed_at = clock()
        if type(observed_at) is not int or not 0 <= observed_at - started <= 30:
            raise ValueError("PREFLIGHT_STALE_OR_CLOCK_INVALID")
        report.update(authenticated_reads_succeeded=balances_ok and orders_ok, observed_at=observed_at)
    except (EOFError, KeyboardInterrupt):
        report["blockers"].append("CANCELLED")
    except Exception as error:
        report["blockers"].append(_reason(error))
    return report


def _format(value):
    """Categorical metadata only; never echo unknown provider values/keys."""
    if value is None:
        return "NULL"
    if type(value) is dict:
        return "OBJECT"
    if type(value) is int:
        return "INTEGER"
    if type(value) is str:
        return "STRING"
    if type(value) is list:
        return "ARRAY"
    return "OTHER"


def _reason(error):
    if isinstance(error, TimeoutError):
        return "READ_TIMEOUT"
    if type(getattr(error, "reason", None)) is ReadReason:
        return error.reason.value
    if isinstance(error, (json.JSONDecodeError, UnicodeError, KeyError, TypeError)):
        return "MALFORMED_RESPONSE"
    if isinstance(error, OSError):
        return "READ_TRANSPORT_FAILED"
    value = str(error)
    return value if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", value) else "READ_TRANSPORT_OR_RESPONSE_FAILED"


def _discovered_accounts(payload, symbol, base, quote):
    """Validate one complete discovery page, retaining IDs only in memory."""
    if (any(type(v) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", v) is None for v in (symbol, base, quote))
            or symbol != base + quote or base == quote):
        raise ValueError("INVALID_MARKET_IDENTITY")
    rows, info = payload.get("result"), payload.get("result_info")
    if (type(rows) is not list or not 1 <= len(rows) <= 100 or type(info) is not dict
            or any(type(info.get(k)) is not int for k in ("page", "count", "total_count"))
            or info["page"] != 1 or info["count"] != info["total_count"] or info["count"] != len(rows)):
        raise ValueError("INCOMPLETE_DISCOVERY")
    options, identities = [], set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("MALFORMED_ACCOUNT_IDENTITY")
        client, user = row.get("clientId"), row.get("userId")
        if (type(client) is not str or not 1 <= len(client) <= 200
                or not all(33 <= ord(c) <= 126 for c in client) or client in identities
                or user_id_format(user) not in ("INTEGER", "DECIMAL_STRING")
                or int(user) < 1 or len(str(user)) > 18
                or type(row.get("isActive")) is not bool or type(row.get("isSuspend")) is not bool):
            raise ValueError("MALFORMED_OR_DUPLICATE_ACCOUNT_IDENTITY")
        identities.add(client)
        if row.get("type") == "prop" and row["isActive"] and not row["isSuspend"]:
            options.append(SpotAccountConfig(client, int(user), symbol, base, quote, True))
    if not options:
        raise ValueError("NO_ACTIVE_UNSUSPENDED_PROP_ACCOUNT")
    return tuple(options)


def _select_account(options):
    print("Active prop accounts (IDs masked; no main-account fallback):", file=sys.stderr)
    for index, option in enumerate(options, 1):
        tail = "..." + option.sub_account_client_id[-4:] if len(option.sub_account_client_id) > 4 else "REDACTED"
        print(f"{index}: type=prop active=true suspended=false clientId={tail} userId=REDACTED", file=sys.stderr)
    print("Verify the masked account in Wallex. Confirm ONLY the intended Hackathon account and its dedicated key.", file=sys.stderr)
    print("Type HACKATHON <number> to confirm, or press Enter to cancel: ", end="", file=sys.stderr, flush=True)
    answer = input()
    if re.fullmatch(r"HACKATHON [1-9][0-9]{0,2}", answer) is None:
        raise ValueError("ACCOUNT_SELECTION_NOT_CONFIRMED")
    index = int(answer.split()[1])
    if not 1 <= index <= len(options):
        raise ValueError("ACCOUNT_SELECTION_NOT_CONFIRMED")
    return options[index - 1]


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print(canonical_json(_report() | {"blockers": ["INVALID_ARGUMENTS"]}))
        raise SystemExit(2)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--connect", action="store_true", required=True)
    for name in ("symbol", "base", "quote"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        report = _report() | {"blockers": ["INTERACTIVE_TERMINAL_REQUIRED"]}
    else:
        report = check_account(connect=args.connect, resolve_credential=_prompt_credential,
                               select_account=_select_account, symbol=args.symbol,
                               base_asset=args.base, quote_asset=args.quote)
    print(canonical_json(report))
    return 0 if report["authenticated_reads_succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
