"""Pure, bounded, conservative spot sizing; never complete risk authorization.

Quote-unit inputs and base-unit output are supplied explicitly. No account,
market, strategy, execution, audit, or shared-contract imports or I/O occur.
"""

from dataclasses import dataclass, field
from decimal import (
    Clamped, Context, Decimal, DecimalException, DivisionByZero, Inexact,
    InvalidOperation, Overflow, ROUND_FLOOR, ROUND_HALF_EVEN, Rounded,
    Subnormal, Underflow, localcontext,
)
from enum import Enum
from fractions import Fraction

MAX_INPUT_DIGITS = 100
MAX_INPUT_EXPONENT = 200
QUANTITY_PRECISION = 34


class RiskProfile(str, Enum):
    L0 = "L0"
    R0 = "R0"


@dataclass(frozen=True)
class SizingPolicy:
    profile: RiskProfile
    risk_fraction: Decimal
    max_position_fraction: Decimal


def policy_for(profile: RiskProfile) -> SizingPolicy:
    """Explicit profile lookup only; no default or active global policy."""
    if profile is RiskProfile.L0:
        return SizingPolicy(profile, Decimal("0.001"), Decimal("0.20"))
    if profile is RiskProfile.R0:
        return SizingPolicy(profile, Decimal("0.0025"), Decimal("0.30"))
    raise ValueError("Explicit RiskProfile.L0 or RiskProfile.R0 is required")


@dataclass(frozen=True)
class SizingInputs:
    """Provisional quote units, quote/base prices, base-unit sizing parameters.

    None represents missing evidence, never an inferred balance, price or policy.
    extra loss includes the caller's worst-case additional quote loss per base.
    Numeric costs are provisional claims, not verified fees/slippage evidence.
    """

    equity: Decimal | None = None
    free_quote: Decimal | None = None
    quote_asset: str | None = None
    pe: Decimal | None = None
    initial_stop: Decimal | None = None
    worst_case_extra_loss_per_unit: Decimal | None = None
    risk_fraction: Decimal | None = None
    max_position_fraction: Decimal | None = None


class SizingReason(str, Enum):
    CALCULATION_VALID = "CALCULATION_VALID"
    PROVISIONAL_COST_EVIDENCE = "PROVISIONAL_COST_EVIDENCE"
    NOT_TRADING_AUTHORIZATION = "NOT_TRADING_AUTHORIZATION"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_EQUITY = "INVALID_EQUITY"
    INVALID_FREE_QUOTE = "INVALID_FREE_QUOTE"
    INVALID_QUOTE_ASSET = "INVALID_QUOTE_ASSET"
    INVALID_PE = "INVALID_PE"
    INVALID_STOP = "INVALID_STOP"
    STOP_NOT_BELOW_PE = "STOP_NOT_BELOW_PE"
    MISSING_COST_EVIDENCE = "MISSING_COST_EVIDENCE"
    INVALID_COST = "INVALID_COST"
    MISSING_POLICY = "MISSING_POLICY"
    INVALID_RISK_FRACTION = "INVALID_RISK_FRACTION"
    INVALID_POSITION_FRACTION = "INVALID_POSITION_FRACTION"
    NUMERIC_RANGE_EXCEEDED = "NUMERIC_RANGE_EXCEEDED"
    ZERO_BUYING_CAPACITY = "ZERO_BUYING_CAPACITY"
    ARITHMETIC_FAILURE = "ARITHMETIC_FAILURE"
    PRECISION_FAILURE = "PRECISION_FAILURE"
    BUDGET_VIOLATION = "BUDGET_VIOLATION"
    NON_POSITIVE_QUANTITY = "NON_POSITIVE_QUANTITY"


@dataclass(frozen=True)
class SizingAssessment:
    """A theoretical cap only; every failure leaves all calculation fields None."""

    calculation_valid: bool
    reasons: tuple[SizingReason, ...]
    inputs: SizingInputs | None = None
    unit_loss: Decimal | None = None
    risk_budget: Decimal | None = None
    position_budget: Decimal | None = None
    notional_budget: Decimal | None = None
    theoretical_quantity_upper_bound: Decimal | None = None
    rejected_field: str | None = None
    executable_ready: bool = field(default=False, init=False)


class _Rejected(ValueError):
    def __init__(self, reason: SizingReason, name: str):
        self.reason, self.name = reason, name
        super().__init__(reason.value)


def _number(value: object, *, name: str, reason: SizingReason, zero: bool = False) -> None:
    if (not isinstance(value, Decimal) or not value.is_finite()
            or value < 0 or (not zero and value == 0)):
        raise _Rejected(reason, name)
    parts = value.as_tuple()
    if len(parts.digits) > MAX_INPUT_DIGITS or abs(parts.exponent) > MAX_INPUT_EXPONENT:
        raise _Rejected(SizingReason.NUMERIC_RANGE_EXCEEDED, name)


def _validate(inputs: SizingInputs) -> None:
    for name, reason, zero in (
        ("equity", SizingReason.INVALID_EQUITY, False),
        ("free_quote", SizingReason.INVALID_FREE_QUOTE, True),
        ("pe", SizingReason.INVALID_PE, False),
        ("initial_stop", SizingReason.INVALID_STOP, False),
    ):
        _number(getattr(inputs, name), name=name, reason=reason, zero=zero)
    asset = inputs.quote_asset
    if (type(asset) is not str or not asset or len(asset) > 200
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in asset)):
        raise _Rejected(SizingReason.INVALID_QUOTE_ASSET, "quote_asset")
    if inputs.initial_stop >= inputs.pe:
        raise _Rejected(SizingReason.STOP_NOT_BELOW_PE, "initial_stop")
    if inputs.worst_case_extra_loss_per_unit is None:
        raise _Rejected(SizingReason.MISSING_COST_EVIDENCE, "worst_case_extra_loss_per_unit")
    _number(inputs.worst_case_extra_loss_per_unit, name="worst_case_extra_loss_per_unit",
            reason=SizingReason.INVALID_COST, zero=True)
    for name, reason in (("risk_fraction", SizingReason.INVALID_RISK_FRACTION),
                         ("max_position_fraction", SizingReason.INVALID_POSITION_FRACTION)):
        value = getattr(inputs, name)
        if value is None:
            raise _Rejected(SizingReason.MISSING_POLICY, name)
        _number(value, name=name, reason=reason)
        if value > 1:
            raise _Rejected(reason, name)
    if inputs.free_quote == 0:
        raise _Rejected(SizingReason.ZERO_BUYING_CAPACITY, "free_quote")


def _exact_context() -> Context:
    # Bounded inputs have <=100 coefficient digits and exponent in [-200,200].
    # Aligned price addition/subtraction needs <=501 digits; products need <=200.
    return Context(
        prec=512, rounding=ROUND_HALF_EVEN, Emin=-999, Emax=999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, Subnormal,
               Clamped, Inexact, Rounded],
    )


def _quantity_context() -> Context:
    # Inexact division is intentional; positive results are always rounded DOWN.
    return Context(
        prec=QUANTITY_PRECISION, rounding=ROUND_FLOOR, Emin=-999, Emax=999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, Subnormal, Clamped],
    )


def assess_position_size(inputs: SizingInputs | None = None) -> SizingAssessment:
    """Compute a non-executable base-unit cap from fully explicit local inputs.

    Only the exact frozen SizingInputs container is accepted. Arithmetic contexts
    are private. Exact rational identities and budget inequalities are checked
    against the original inputs after downward quantity division. No instrument
    increments or fee defaults are selected; no other risk gate is invoked.
    """
    if type(inputs) is not SizingInputs:
        return SizingAssessment(False, (SizingReason.INVALID_INPUT,), rejected_field="inputs")
    try:
        _validate(inputs)
        with localcontext(_exact_context()):
            unit_loss = (inputs.pe - inputs.initial_stop) + inputs.worst_case_extra_loss_per_unit
            risk_budget = inputs.equity * inputs.risk_fraction
            position_budget = inputs.equity * inputs.max_position_fraction
            notional_budget = min(position_budget, inputs.free_quote)
        if not all(value.is_finite() and value > 0 for value in
                   (unit_loss, risk_budget, position_budget, notional_budget)):
            raise _Rejected(SizingReason.ARITHMETIC_FAILURE, "budgets")
        with localcontext(_quantity_context()):
            quantity_by_risk = risk_budget / unit_loss
            quantity_by_notional = notional_budget / inputs.pe
            quantity = min(quantity_by_risk, quantity_by_notional)
        if not quantity.is_finite() or quantity <= 0:
            raise _Rejected(SizingReason.NON_POSITIVE_QUANTITY, "quantity")
        # Fraction(Decimal) is exact and independent of all Decimal contexts.
        exact_loss = (Fraction(inputs.pe) - Fraction(inputs.initial_stop)
                      + Fraction(inputs.worst_case_extra_loss_per_unit))
        exact_risk = Fraction(inputs.equity) * Fraction(inputs.risk_fraction)
        exact_position = Fraction(inputs.equity) * Fraction(inputs.max_position_fraction)
        exact_notional = min(exact_position, Fraction(inputs.free_quote))
        if ((Fraction(unit_loss), Fraction(risk_budget), Fraction(position_budget),
             Fraction(notional_budget)) != (exact_loss, exact_risk, exact_position, exact_notional)):
            raise _Rejected(SizingReason.PRECISION_FAILURE, "budgets")
        if (Fraction(quantity) * exact_loss > exact_risk
                or Fraction(quantity) * Fraction(inputs.pe) > exact_notional):
            raise _Rejected(SizingReason.BUDGET_VIOLATION, "quantity")
        return SizingAssessment(
            calculation_valid=True,
            reasons=(SizingReason.CALCULATION_VALID, SizingReason.PROVISIONAL_COST_EVIDENCE,
                     SizingReason.NOT_TRADING_AUTHORIZATION),
            inputs=inputs, unit_loss=unit_loss, risk_budget=risk_budget,
            position_budget=position_budget, notional_budget=notional_budget,
            theoretical_quantity_upper_bound=quantity,
        )
    except _Rejected as exc:
        return SizingAssessment(False, (exc.reason,), rejected_field=exc.name)
    except (DecimalException, OverflowError, ValueError):
        return SizingAssessment(False, (SizingReason.ARITHMETIC_FAILURE,),
                                rejected_field="arithmetic")
