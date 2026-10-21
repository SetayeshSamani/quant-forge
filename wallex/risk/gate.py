"""Fail-closed entry checks over provisional internal structures."""

from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, Underflow, localcontext
from enum import Enum


@dataclass(frozen=True)
class EntryRequest:
    """A quantity/price candidate for checking, not an executable order."""

    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class RiskLimits:
    """Mandatory positive limits in caller-agreed quantity and quote units."""

    max_quantity: Decimal
    max_notional: Decimal


class RiskReason(str, Enum):
    MISSING_CONFIG = "MISSING_CONFIG"
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_INPUT = "INVALID_INPUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    WITHIN_LIMITS = "WITHIN_LIMITS"


@dataclass(frozen=True)
class RiskDecision:
    """A validation result only; allowed does not authorize live execution."""

    allowed: bool
    reason: RiskReason


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def evaluate_entry(
    request: EntryRequest | None = None, limits: RiskLimits | None = None
) -> RiskDecision:
    """Reject missing/malformed values and quantities or notionals above limits.

    No environment configuration is loaded. No order is built or submitted.
    Decimal multiplication must be exact; arithmetic failures deny entry.
    """
    if limits is None:
        return RiskDecision(False, RiskReason.MISSING_CONFIG)
    if not isinstance(limits, RiskLimits) or not (
        _positive_decimal(limits.max_quantity)
        and _positive_decimal(limits.max_notional)
    ):
        return RiskDecision(False, RiskReason.INVALID_CONFIG)
    if not isinstance(request, EntryRequest) or not (
        _positive_decimal(request.quantity) and _positive_decimal(request.price)
    ):
        return RiskDecision(False, RiskReason.INVALID_INPUT)
    if request.quantity > limits.max_quantity:
        return RiskDecision(False, RiskReason.LIMIT_EXCEEDED)
    try:
        with localcontext() as context:
            context.prec = max(
                28,
                len(request.quantity.as_tuple().digits)
                + len(request.price.as_tuple().digits),
            )
            context.traps[Inexact] = True
            context.traps[Underflow] = True
            notional = request.quantity * request.price
    except DecimalException:
        return RiskDecision(False, RiskReason.INVALID_INPUT)
    if not _positive_decimal(notional):
        return RiskDecision(False, RiskReason.INVALID_INPUT)
    if notional > limits.max_notional:
        return RiskDecision(False, RiskReason.LIMIT_EXCEEDED)
    return RiskDecision(True, RiskReason.WITHIN_LIMITS)
