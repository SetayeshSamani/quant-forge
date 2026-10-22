"""Pure offline credit evidence projection, never account/risk authorization.

Only sanitized payloads belong here. No key, HTTP, clock, ledger, Risk or order
interface is accepted. Field semantics are explicit unapproved caller claims.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import re

from wallex.strategy.engine import Signal


class AccountKind(str, Enum):
    TRADE_CREDIT = "TRADE_CREDIT"


@dataclass(frozen=True)
class CreditAccountBinding:
    sub_account_client_id: str
    owner_user_id: int
    package_id: int
    user_package_id: int
    currency: str
    kind: AccountKind
    plan_type: str


@dataclass(frozen=True)
class CreditResponse:
    """Supplied request-scope/observation claims; payload must stay stable in-call.

    No authentication headers/keys are accepted. This wrapper cannot prove which
    credential fetched a response, nor that three responses form an atomic view.
    """
    payload: object
    sub_account_client_id: str
    observed_at: int
    complete: bool


@dataclass(frozen=True)
class CreditSemantics:
    """Local wire-field interpretation, NOT approved Wallex credit-risk policy.

    The only supported threshold basis is ACCOUNT_VALUE_IN_QUOTE. The only debt
    mapping is an explicit FINAL_PAYBACK_AS_TOTAL_DUE claim. Missing/other meaning
    fails closed; neither interpretation is inferred from a field name.
    """
    active_plan_status: str
    balance_units: tuple[tuple[str, str], ...]
    fee_currency: str
    debt_currency: str
    threshold_currency: str
    threshold_basis: str
    debt_basis: str


@dataclass(frozen=True)
class CreditEvidenceInputs:
    binding: CreditAccountBinding
    accounts: CreditResponse
    plan: CreditResponse
    balances: CreditResponse
    semantics: CreditSemantics
    as_of: int
    max_age_seconds: int


@dataclass(frozen=True)
class CreditBalance:
    currency: str
    unit: str
    total: Decimal
    locked: Decimal
    available: Decimal


@dataclass(frozen=True)
class CreditAccountSnapshot:
    binding: CreditAccountBinding
    plan_status: str
    balances: tuple[CreditBalance, ...]
    collateral: Decimal
    loan_principal: Decimal
    outstanding_debt_claim: Decimal
    daily_fee: Decimal
    daily_fee_discount: Decimal
    daily_fee_after_discount: Decimal
    payable_fee: Decimal
    warning_limit: Decimal
    liquidation_limit: Decimal
    semantics: CreditSemantics
    created_at: int
    expires_at: int
    as_of: int
    observed_times: tuple[tuple[str, int], ...]
    risk_equity: None = field(default=None, init=False)

    @property
    def available_quote(self) -> Decimal:
        """Reported available quote units, not equity, sized funds or fee approval."""
        return next(b.available for b in self.balances if b.currency == self.binding.currency)


class CreditReason(str, Enum):
    LOCAL_EVIDENCE_VALID = "LOCAL_EVIDENCE_VALID"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INACTIVE_OR_SUSPENDED = "INACTIVE_OR_SUSPENDED"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    FUTURE_EVIDENCE = "FUTURE_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    EXPIRED_PLAN = "EXPIRED_PLAN"
    AMBIGUOUS_UNITS_OR_SEMANTICS = "AMBIGUOUS_UNITS_OR_SEMANTICS"
    INVALID_NUMBER = "INVALID_NUMBER"
    INCONSISTENT_BALANCES = "INCONSISTENT_BALANCES"
    INCONSISTENT_TERMS = "INCONSISTENT_TERMS"


@dataclass(frozen=True)
class CreditAssessment:
    snapshot_valid: bool
    reasons: tuple[CreditReason, ...]
    snapshot: CreditAccountSnapshot | None = None
    rejected_field: str | None = None
    blocks_new_entries: bool = field(default=True, init=False)
    credential_scope_verified: bool = field(default=False, init=False)
    executable_ready: bool = field(default=False, init=False)
    executable_action: Signal = field(default=Signal.NO_TRADE, init=False)
    limitations: tuple[str, ...] = field(default=(
        "OFFLINE_SOURCE_CLAIMS", "CREDENTIAL_SCOPE_UNVERIFIED",
        "CREDIT_SEMANTICS_UNAPPROVED", "NON_ATOMIC_SNAPSHOT",
        "NOT_PORTFOLIO_OR_RISK_APPROVAL",
    ), init=False)


class _Rejected(ValueError):
    def __init__(self, reason, name):
        self.reason, self.name = reason, name


def _require(condition, reason, name):
    if not condition:
        raise _Rejected(reason, name)


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 200
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


def _integer(value):
    return type(value) is int and value >= 0


def user_id_format(value):
    """Categorize wire identity without returning the identifier itself."""
    if type(value) is int:
        return "INTEGER"
    if value is None:
        return "NULL"
    if (type(value) is str and value.isascii() and value.isdecimal()
            and (value == "0" or not value.startswith("0"))):
        return "DECIMAL_STRING"
    return "OTHER"


def user_id_matches(value, expected):
    """Exact nonnegative identity match; no coercion, rounding or zero stripping."""
    return _integer(expected) and (
        (_integer(value) and value == expected)
        or (user_id_format(value) == "DECIMAL_STRING" and value == str(expected))
    )


def _get(record, name):
    _require(isinstance(record, Mapping) and name in record,
             CreditReason.MISSING_EVIDENCE, name)
    return record[name]


def _number(record, name):
    value = _get(record, name)
    _require(type(value) is str and len(value) <= 200 and re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is not None,
        CreditReason.INVALID_NUMBER, name)
    value = Decimal(value)  # Exact ASCII wire text only; no floats/context rounding.
    parts = value.as_tuple()
    _require(value.is_finite() and value >= 0 and len(parts.digits) <= 100
             and abs(parts.exponent) <= 200, CreditReason.INVALID_NUMBER, name)
    return value


def _utc_seconds(value, name):
    _require(type(value) is str and len(value) <= 40, CreditReason.INVALID_TIMESTAMP, name)
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(None) or stamp.microsecond:
            raise ValueError
        delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds = delta.days * 86400 + delta.seconds
        if seconds < 0:
            raise ValueError
        return seconds
    except (ValueError, OverflowError):
        raise _Rejected(CreditReason.INVALID_TIMESTAMP, name) from None


def _response(response, inputs, name):
    _require(type(response) is CreditResponse, CreditReason.MISSING_EVIDENCE, name)
    _require(response.sub_account_client_id == inputs.binding.sub_account_client_id,
             CreditReason.IDENTITY_MISMATCH, name + ".sub_account_client_id")
    _require(response.complete is True, CreditReason.INCOMPLETE_EVIDENCE, name + ".complete")
    _require(_integer(response.observed_at), CreditReason.INVALID_TIMESTAMP, name + ".observed_at")
    age = inputs.as_of - response.observed_at
    _require(age >= 0, CreditReason.FUTURE_EVIDENCE, name)
    _require(age <= inputs.max_age_seconds, CreditReason.STALE_EVIDENCE, name)
    _require(_get(response.payload, "success") is True, CreditReason.INCOMPLETE_EVIDENCE, name)
    return _get(response.payload, "result")


def assess_credit_account(inputs: CreditEvidenceInputs | None = None) -> CreditAssessment:
    """Validate explicit, stable, sanitized evidence without inferring equity/scope.

    Success means local fixture checks only. It never passes data to Risk, creates
    an Execution AccountSnapshot, or authorizes new entries; all actions NO_TRADE.
    """
    r = CreditReason
    try:
        _require(type(inputs) is CreditEvidenceInputs, r.INVALID_INPUT, "inputs")
        binding, semantics = inputs.binding, inputs.semantics
        _require(type(binding) is CreditAccountBinding, r.INVALID_INPUT, "binding")
        _require(binding.kind is AccountKind.TRADE_CREDIT and _identifier(binding.sub_account_client_id)
                 and _integer(binding.owner_user_id) and _integer(binding.package_id)
                 and binding.package_id > 0 and _integer(binding.user_package_id)
                 and binding.user_package_id > 0 and _identifier(binding.plan_type),
                 r.IDENTITY_MISMATCH, "binding")
        _require(binding.currency in ("TMN", "USDT"), r.AMBIGUOUS_UNITS_OR_SEMANTICS, "currency")
        _require(_integer(inputs.as_of) and _integer(inputs.max_age_seconds), r.INVALID_TIMESTAMP, "as_of/age")
        _require(type(semantics) is CreditSemantics, r.AMBIGUOUS_UNITS_OR_SEMANTICS, "semantics")
        _require(_identifier(semantics.active_plan_status)
                 and semantics.fee_currency == semantics.debt_currency == semantics.threshold_currency == binding.currency
                 and semantics.threshold_basis == "ACCOUNT_VALUE_IN_QUOTE"
                 and semantics.debt_basis == "FINAL_PAYBACK_AS_TOTAL_DUE",
                 r.AMBIGUOUS_UNITS_OR_SEMANTICS, "semantics")
        _require(type(semantics.balance_units) is tuple and bool(semantics.balance_units),
                 r.AMBIGUOUS_UNITS_OR_SEMANTICS, "balance_units")
        units = {}
        for pair in semantics.balance_units:
            _require(type(pair) is tuple and len(pair) == 2 and _identifier(pair[0])
                     and pair[0] == pair[1] and pair[0] not in units,
                     r.AMBIGUOUS_UNITS_OR_SEMANTICS, "balance_units")
            units[pair[0]] = pair[1]

        accounts = _response(inputs.accounts, inputs, "accounts")
        _require(type(accounts) is list and bool(accounts), r.INCOMPLETE_EVIDENCE, "accounts.result")
        info = _get(inputs.accounts.payload, "result_info")
        _require(_get(info, "page") == 1 and type(info['page']) is int
                 and _get(info, "count") == len(accounts) and type(info['count']) is int
                 and _get(info, "total_count") == len(accounts) and type(info['total_count']) is int,
                 r.INCOMPLETE_EVIDENCE, "accounts.pagination")
        ids = [_get(account, "clientId") for account in accounts]
        _require(all(_identifier(i) for i in ids) and len(set(ids)) == len(ids),
                 r.IDENTITY_MISMATCH, "accounts.clientId")
        _require(binding.sub_account_client_id in ids, r.IDENTITY_MISMATCH, "accounts.clientId")
        account = accounts[ids.index(binding.sub_account_client_id)]
        _require(user_id_matches(_get(account, "userId"), binding.owner_user_id)
                 and _get(account, "type") == "prop", r.IDENTITY_MISMATCH, "accounts.owner/type")
        _require(_get(account, "isActive") is True and _get(account, "isSuspend") is False,
                 r.INACTIVE_OR_SUSPENDED, "accounts.status")

        plan = _response(inputs.plan, inputs, "plan")
        package, active = _get(plan, "packageDetail"), _get(plan, "userPackageDetail")
        _require(type(_get(package, "id")) is int and package['id'] == binding.package_id
                 and type(_get(active, "id")) is int and active['id'] == binding.user_package_id
                 and _get(active, "type") == binding.plan_type, r.IDENTITY_MISMATCH, "plan.identity")
        _require(_get(package, "currency") == binding.currency
                 and _get(package, "destination_currency") in (None, binding.currency),
                 r.AMBIGUOUS_UNITS_OR_SEMANTICS, "plan.currency")
        status = _get(active, "status")
        _require(type(status) is str and status == semantics.active_plan_status
                 and status.casefold() not in {"inactive", "suspended", "expired", "closed",
                                               "liquid", "liquidated", "canceled", "cancelled"},
                 r.INACTIVE_OR_SUSPENDED, "plan.status")
        created, expires = (_utc_seconds(_get(active, name), name) for name in ("createdAt", "expireAt"))
        _require(created <= inputs.plan.observed_at and expires > created, r.INVALID_TIMESTAMP, "plan.times")
        _require(expires > inputs.as_of, r.EXPIRED_PLAN, "expireAt")

        balances = _response(inputs.balances, inputs, "balances")
        _require(isinstance(balances, Mapping) and set(balances) == set(units)
                 and binding.currency in balances, r.INCONSISTENT_BALANCES, "balances.currencies")
        rows = []
        for currency in sorted(balances):
            raw = balances[currency]
            _require(_get(raw, "symbol") == currency, r.AMBIGUOUS_UNITS_OR_SEMANTICS, "balance.symbol")
            total, locked, available = (_number(raw, name) for name in ("total", "freeze", "available"))
            _require(Fraction(total) == Fraction(locked) + Fraction(available), r.INCONSISTENT_BALANCES, currency)
            rows.append(CreditBalance(currency, units[currency], total, locked, available))
        collateral, principal = (_number(package, name) for name in ("assurance", "loan"))
        daily, discount, after = (_number(package, name) for name in ("dailyFee", "dailyFeeDiscount", "dailyFeeAfterDiscount"))
        debt, payable, warning, liquidation = (_number(active, name) for name in
                                              ("finalPayback", "finalPayableFeeAmount", "warningLimit", "liquidityLimit"))
        _require(collateral > 0 and principal > 0 and Fraction(daily) == Fraction(discount) + Fraction(after)
                 and warning > liquidation > 0, r.INCONSISTENT_TERMS, "credit_terms")
        snapshot = CreditAccountSnapshot(
            binding, status, tuple(rows), collateral, principal, debt, daily, discount, after,
            payable, warning, liquidation, semantics, created, expires, inputs.as_of,
            tuple((name, getattr(inputs, name).observed_at) for name in ("accounts", "plan", "balances")),
        )
        return CreditAssessment(True, (r.LOCAL_EVIDENCE_VALID,), snapshot)
    except _Rejected as exc:
        return CreditAssessment(False, (exc.reason,), rejected_field=exc.name)
    except Exception:
        # No raw response, foreign exception or partially usable balance escapes.
        return CreditAssessment(False, (r.INVALID_INPUT,), rejected_field="inputs")
