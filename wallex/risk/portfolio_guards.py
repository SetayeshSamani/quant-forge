"""Read-only account-wide checks for new BUY entries; never atomic admission.

No accounts, orders, reservations, locks, clocks, or other risk checks are invoked.
A passing snapshot must be revalidated in the agreed durable admission protocol.
"""

from dataclasses import dataclass, field
from enum import Enum

_COUNT_FIELDS = (
    "open_position_count", "outstanding_order_count", "uncertain_order_count",
    "held_reservation_count", "unsettled_fill_count",
)
_COMPLETENESS_FIELDS = (
    "positions_complete", "orders_complete", "reservations_complete",
    "settlement_complete", "account_scope_complete",
)


@dataclass(frozen=True)
class PortfolioGuardState:
    """Explicit provisional account aggregate, not a verified shared account model.

    Counts cover the whole relevant trading account, never just one symbol or
    quote asset. None is missing evidence, never zero or a completeness claim.
    Age policy is caller-supplied in seconds, not an approved global default.
    """

    account_id: str | None = None
    quote_asset: str | None = None
    as_of: int | None = None
    snapshot_observed_at: int | None = None
    max_snapshot_age_seconds: int | None = None
    open_position_count: int | None = None
    outstanding_order_count: int | None = None
    uncertain_order_count: int | None = None
    held_reservation_count: int | None = None
    unsettled_fill_count: int | None = None
    positions_complete: bool | None = None
    orders_complete: bool | None = None
    reservations_complete: bool | None = None
    settlement_complete: bool | None = None
    account_scope_complete: bool | None = None


class PortfolioGuardReason(str, Enum):
    OPEN_POSITION_EXISTS = "OPEN_POSITION_EXISTS"
    MULTIPLE_POSITIONS_DETECTED = "MULTIPLE_POSITIONS_DETECTED"
    OUTSTANDING_ORDER_EXISTS = "OUTSTANDING_ORDER_EXISTS"
    UNCERTAIN_ORDER_EXISTS = "UNCERTAIN_ORDER_EXISTS"
    HELD_RESERVATION_EXISTS = "HELD_RESERVATION_EXISTS"
    UNSETTLED_FILL_EXISTS = "UNSETTLED_FILL_EXISTS"
    INCOMPLETE_ACCOUNT_EVIDENCE = "INCOMPLETE_ACCOUNT_EVIDENCE"
    INVALID_ACCOUNT_STATE = "INVALID_ACCOUNT_STATE"
    INVALID_SNAPSHOT_AGE_POLICY = "INVALID_SNAPSHOT_AGE_POLICY"
    FUTURE_ACCOUNT_SNAPSHOT = "FUTURE_ACCOUNT_SNAPSHOT"
    STALE_ACCOUNT_SNAPSHOT = "STALE_ACCOUNT_SNAPSHOT"
    NO_LOCAL_PORTFOLIO_BLOCKER = "NO_LOCAL_PORTFOLIO_BLOCKER"


@dataclass(frozen=True)
class PortfolioGuardAssessment:
    """snapshot_valid and no local blocker never constitute full risk approval.

    Invalid/incomplete/stale evidence blocks entries and is not returned as a
    trusted state. This output does not govern exits, cancellation or recovery.
    """

    snapshot_valid: bool
    reasons: tuple[PortfolioGuardReason, ...]
    blocks_new_entries: bool = True
    state: PortfolioGuardState | None = None
    snapshot_age_seconds: int | None = None
    rejected_field: str | None = None
    executable_ready: bool = field(default=False, init=False)


def _reject(reason: PortfolioGuardReason, name: str) -> PortfolioGuardAssessment:
    return PortfolioGuardAssessment(False, (reason,), rejected_field=name)


def assess_portfolio_entry(state: PortfolioGuardState | None = None) -> PortfolioGuardAssessment:
    """Examine one immutable snapshot for new BUY blockers without consuming it.

    Age equality is permitted; zero maximum age requires observation at as_of.
    Repeated callers can both pass the same snapshot. No reservation, identity,
    admission token, write, or order is produced. All readiness stays disabled.
    """
    reason = PortfolioGuardReason
    if state is None:
        return _reject(reason.INCOMPLETE_ACCOUNT_EVIDENCE, "state")
    if type(state) is not PortfolioGuardState:
        return _reject(reason.INVALID_ACCOUNT_STATE, "state")
    for name in ("account_id", "quote_asset", "as_of", "snapshot_observed_at",
                 "max_snapshot_age_seconds", *_COUNT_FIELDS, *_COMPLETENESS_FIELDS):
        if getattr(state, name) is None:
            return _reject(reason.INCOMPLETE_ACCOUNT_EVIDENCE, name)
    for name in ("account_id", "quote_asset"):
        value = getattr(state, name)
        if (type(value) is not str or not value or value.strip() != value or len(value) > 200
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or (name == "quote_asset" and any(c.isspace() for c in value))):
            return _reject(reason.INVALID_ACCOUNT_STATE, name)
    for name in _COUNT_FIELDS:
        value = getattr(state, name)
        if type(value) is not int or value < 0:
            return _reject(reason.INVALID_ACCOUNT_STATE, name)
    for name in _COMPLETENESS_FIELDS:
        value = getattr(state, name)
        if type(value) is not bool:
            return _reject(reason.INVALID_ACCOUNT_STATE, name)
        if not value:
            return _reject(reason.INCOMPLETE_ACCOUNT_EVIDENCE, name)
    for name in ("as_of", "snapshot_observed_at"):
        value = getattr(state, name)
        if type(value) is not int or value < 0:
            return _reject(reason.INVALID_ACCOUNT_STATE, name)
    if type(state.max_snapshot_age_seconds) is not int or state.max_snapshot_age_seconds < 0:
        return _reject(reason.INVALID_SNAPSHOT_AGE_POLICY, "max_snapshot_age_seconds")
    if state.snapshot_observed_at > state.as_of:
        return _reject(reason.FUTURE_ACCOUNT_SNAPSHOT, "snapshot_observed_at")
    age = state.as_of - state.snapshot_observed_at
    if age > state.max_snapshot_age_seconds:
        return _reject(reason.STALE_ACCOUNT_SNAPSHOT, "snapshot_observed_at")
    reasons = []
    if state.open_position_count > 0:
        reasons.append(reason.OPEN_POSITION_EXISTS)
        if state.open_position_count > 1:
            reasons.append(reason.MULTIPLE_POSITIONS_DETECTED)
    for name, code in zip(_COUNT_FIELDS[1:], (
        reason.OUTSTANDING_ORDER_EXISTS, reason.UNCERTAIN_ORDER_EXISTS,
        reason.HELD_RESERVATION_EXISTS, reason.UNSETTLED_FILL_EXISTS,
    )):
        if getattr(state, name) > 0:
            reasons.append(code)
    return PortfolioGuardAssessment(
        snapshot_valid=True, reasons=tuple(reasons) or (reason.NO_LOCAL_PORTFOLIO_BLOCKER,),
        blocks_new_entries=bool(reasons), state=state, snapshot_age_seconds=age,
    )
