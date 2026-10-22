"""Pure UTC daily-equity loss and peak-equity drawdown checks, not authorization.

All valuations, high-water history and breach flags are caller-supplied evidence.
No state is fetched, persisted, reset, or passed to sizing, gate, or execution.
"""

from dataclasses import dataclass, field
from decimal import (
    Clamped, Context, Decimal, DecimalException, DivisionByZero, Inexact,
    InvalidOperation, Overflow, ROUND_HALF_EVEN, Rounded, Subnormal,
    Underflow, localcontext,
)
from enum import Enum
from fractions import Fraction

from .sizing import MAX_INPUT_DIGITS, MAX_INPUT_EXPONENT, RiskProfile

UTC_DAY_SECONDS = 86400


@dataclass(frozen=True)
class LossGuardPolicy:
    profile: RiskProfile
    daily_loss_fraction: Decimal
    max_drawdown_fraction: Decimal


def loss_policy_for(profile: RiskProfile) -> LossGuardPolicy:
    """Return only the explicit agreed profile; never activate a default."""
    if profile is RiskProfile.L0:
        return LossGuardPolicy(profile, Decimal("0.005"), Decimal("0.01"))
    if profile is RiskProfile.R0:
        return LossGuardPolicy(profile, Decimal("0.01"), Decimal("0.02"))
    raise ValueError("Explicit RiskProfile.L0 or RiskProfile.R0 is required")


@dataclass(frozen=True)
class LossGuardState:
    """Provisional quote-unit evidence; None means missing, never False or zero.

    as_of, day_start_at and equity_observed_at are UTC Unix seconds. Observation
    must belong to the current UTC day and be no later than as_of. High-water and
    prior flags must come from verified persistent history in future integration;
    this container itself cannot establish source authenticity or live freshness.
    """

    current_equity: Decimal | None = None
    day_start_equity: Decimal | None = None
    high_water_equity: Decimal | None = None
    quote_asset: str | None = None
    as_of: int | None = None
    day_start_at: int | None = None
    equity_observed_at: int | None = None
    policy: LossGuardPolicy | None = None
    prior_daily_breach: bool | None = None
    prior_drawdown_breach: bool | None = None


class LossGuardReason(str, Enum):
    DAILY_LOSS_LIMIT_REACHED = "DAILY_LOSS_LIMIT_REACHED"
    DRAWDOWN_LIMIT_REACHED = "DRAWDOWN_LIMIT_REACHED"
    PREVIOUS_DAILY_BREACH = "PREVIOUS_DAILY_BREACH"
    PREVIOUS_DRAWDOWN_BREACH = "PREVIOUS_DRAWDOWN_BREACH"
    MISSING_STATE = "MISSING_STATE"
    INVALID_STATE = "INVALID_STATE"
    INVALID_POLICY = "INVALID_POLICY"
    INCONSISTENT_TIMESTAMPS = "INCONSISTENT_TIMESTAMPS"
    INVALID_EQUITY_BASELINE = "INVALID_EQUITY_BASELINE"
    NUMERIC_RANGE_EXCEEDED = "NUMERIC_RANGE_EXCEEDED"
    ARITHMETIC_FAILURE = "ARITHMETIC_FAILURE"
    PRECISION_FAILURE = "PRECISION_FAILURE"
    WITHIN_LOCAL_LIMITS = "WITHIN_LOCAL_LIMITS"


@dataclass(frozen=True)
class LossGuardAssessment:
    """blocks_new_entries=False means these local checks only, never approval.

    Valid breached states retain exact evidence and proposed latched flags for a
    future persistent adapter. Invalid states return none of that evidence and
    block entries. This module never writes the returned latches to storage.
    """

    calculation_valid: bool
    reasons: tuple[LossGuardReason, ...]
    blocks_new_entries: bool = True
    state: LossGuardState | None = None
    daily_loss: Decimal | None = None
    daily_loss_budget: Decimal | None = None
    current_drawdown: Decimal | None = None
    drawdown_budget: Decimal | None = None
    daily_breach_latched: bool | None = None
    drawdown_breach_latched: bool | None = None
    rejected_field: str | None = None
    executable_ready: bool = field(default=False, init=False)


class _Rejected(ValueError):
    def __init__(self, reason: LossGuardReason, name: str):
        self.reason, self.name = reason, name
        super().__init__(reason.value)


def _bounded(value: Decimal) -> bool:
    parts = value.as_tuple()
    return len(parts.digits) <= MAX_INPUT_DIGITS and abs(parts.exponent) <= MAX_INPUT_EXPONENT


def _validate_policy(policy: object) -> None:
    if type(policy) is not LossGuardPolicy:
        raise _Rejected(LossGuardReason.INVALID_POLICY, "policy")
    try:
        expected = loss_policy_for(policy.profile)
    except ValueError as exc:
        raise _Rejected(LossGuardReason.INVALID_POLICY, "policy.profile") from exc
    for name in ("daily_loss_fraction", "max_drawdown_fraction"):
        value = getattr(policy, name)
        if (not isinstance(value, Decimal) or not value.is_finite() or not _bounded(value)
                or value != getattr(expected, name)):
            raise _Rejected(LossGuardReason.INVALID_POLICY, "policy." + name)


def _validate(state: LossGuardState) -> None:
    for name in ("current_equity", "day_start_equity", "high_water_equity", "quote_asset",
                 "as_of", "day_start_at", "equity_observed_at", "policy",
                 "prior_daily_breach", "prior_drawdown_breach"):
        if getattr(state, name) is None:
            raise _Rejected(LossGuardReason.MISSING_STATE, name)
    _validate_policy(state.policy)
    for name in ("current_equity", "day_start_equity", "high_water_equity"):
        value = getattr(state, name)
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise _Rejected(LossGuardReason.INVALID_EQUITY_BASELINE, name)
        if not _bounded(value):
            raise _Rejected(LossGuardReason.NUMERIC_RANGE_EXCEEDED, name)
    if state.high_water_equity < max(state.current_equity, state.day_start_equity):
        raise _Rejected(LossGuardReason.INVALID_EQUITY_BASELINE, "high_water_equity")
    asset = state.quote_asset
    if (type(asset) is not str or not asset or len(asset) > 200
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in asset)):
        raise _Rejected(LossGuardReason.INVALID_STATE, "quote_asset")
    for name in ("prior_daily_breach", "prior_drawdown_breach"):
        if type(getattr(state, name)) is not bool:
            raise _Rejected(LossGuardReason.INVALID_STATE, name)
    for name in ("as_of", "day_start_at", "equity_observed_at"):
        value = getattr(state, name)
        if type(value) is not int or value < 0:
            raise _Rejected(LossGuardReason.INCONSISTENT_TIMESTAMPS, name)
    if state.day_start_at != state.as_of // UTC_DAY_SECONDS * UTC_DAY_SECONDS:
        raise _Rejected(LossGuardReason.INCONSISTENT_TIMESTAMPS, "day_start_at")
    if not state.day_start_at <= state.equity_observed_at <= state.as_of:
        raise _Rejected(LossGuardReason.INCONSISTENT_TIMESTAMPS, "equity_observed_at")


def _exact_context() -> Context:
    # Same bounded numeric domain as sizing; no profile threshold is inferred.
    return Context(
        prec=512, rounding=ROUND_HALF_EVEN, Emin=-999, Emax=999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, Subnormal,
               Clamped, Inexact, Rounded],
    )


def assess_loss_guards(state: LossGuardState | None = None) -> LossGuardAssessment:
    """Block on >= either exact limit, or either prior latch; never reset flags.

    A valid calculation is not a complete portfolio risk check. Malformed input
    blocks entries and exposes no local calculation or apparently usable approval.
    No gate, sizing function, account source, wall clock or order path is called.
    """
    if state is None:
        return LossGuardAssessment(False, (LossGuardReason.MISSING_STATE,), rejected_field="state")
    if type(state) is not LossGuardState:
        return LossGuardAssessment(False, (LossGuardReason.INVALID_STATE,), rejected_field="state")
    try:
        _validate(state)
        with localcontext(_exact_context()):
            daily_loss = max(Decimal(0), state.day_start_equity - state.current_equity)
            daily_budget = state.day_start_equity * state.policy.daily_loss_fraction
            drawdown = max(Decimal(0), state.high_water_equity - state.current_equity)
            drawdown_budget = state.high_water_equity * state.policy.max_drawdown_fraction
        exact = (
            max(Fraction(0), Fraction(state.day_start_equity) - Fraction(state.current_equity)),
            Fraction(state.day_start_equity) * Fraction(state.policy.daily_loss_fraction),
            max(Fraction(0), Fraction(state.high_water_equity) - Fraction(state.current_equity)),
            Fraction(state.high_water_equity) * Fraction(state.policy.max_drawdown_fraction),
        )
        if tuple(Fraction(value) for value in (daily_loss, daily_budget, drawdown, drawdown_budget)) != exact:
            raise _Rejected(LossGuardReason.PRECISION_FAILURE, "calculation")
        daily_breach = exact[0] >= exact[1]
        drawdown_breach = exact[2] >= exact[3]
        reasons = []
        if daily_breach:
            reasons.append(LossGuardReason.DAILY_LOSS_LIMIT_REACHED)
        if drawdown_breach:
            reasons.append(LossGuardReason.DRAWDOWN_LIMIT_REACHED)
        if state.prior_daily_breach:
            reasons.append(LossGuardReason.PREVIOUS_DAILY_BREACH)
        if state.prior_drawdown_breach:
            reasons.append(LossGuardReason.PREVIOUS_DRAWDOWN_BREACH)
        return LossGuardAssessment(
            calculation_valid=True,
            reasons=tuple(reasons) or (LossGuardReason.WITHIN_LOCAL_LIMITS,),
            blocks_new_entries=bool(reasons), state=state,
            daily_loss=daily_loss, daily_loss_budget=daily_budget,
            current_drawdown=drawdown, drawdown_budget=drawdown_budget,
            daily_breach_latched=daily_breach or state.prior_daily_breach,
            drawdown_breach_latched=drawdown_breach or state.prior_drawdown_breach,
        )
    except _Rejected as exc:
        return LossGuardAssessment(False, (exc.reason,), rejected_field=exc.name)
    except (DecimalException, OverflowError, ValueError):
        return LossGuardAssessment(False, (LossGuardReason.ARITHMETIC_FAILURE,),
                                   rejected_field="arithmetic")
