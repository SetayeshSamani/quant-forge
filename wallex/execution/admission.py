"""Synthetic replay admission checks; no venue attestation or LIVE permission."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from wallex.risk.pipeline import PipelineInputs, assess_risk_pipeline
from .models import ExecutionRequest, Mode, Side, utc


@dataclass(frozen=True)
class ReplayAdmission:
    risk_inputs: PipelineInputs
    account_scope_verified: bool
    trade_permission_verified: bool
    market_approved: bool
    market_symbol: str
    base_asset: str
    quote_asset: str
    bid: Decimal
    ask: Decimal
    observed_at: datetime
    max_age_seconds: int
    event_time_verified: bool
    price_step: Decimal
    quantity_step: Decimal
    min_notional: Decimal
    max_notional: Decimal
    fee_quote_rate: Decimal
    opposing_sell_prices: tuple[Decimal, ...]
    order_coverage_complete: bool


@dataclass(frozen=True)
class AdmissionAssessment:
    local_checks_passed: bool
    reasons: tuple[str, ...]
    action: str = field(default="NO_TRADE", init=False)
    executable_ready: bool = field(default=False, init=False)


def assess_replay_admission(request, evidence, *, now):
    """Exact rational checks reuse real risk sizing/loss/portfolio calculations.

    All evidence here is explicitly SYNTHETIC. Flags cannot attest real Wallex
    permissions, event freshness, fees or account reconciliation. The existing
    Gateway still performs durable reservation/admission under its writer lock.
    """
    reasons = []
    try:
        now = utc(now)
        if type(request) is not ExecutionRequest or type(evidence) is not ReplayAdmission:
            raise ValueError()
        intent, e = request.intent, evidence
        if intent.mode is not Mode.REPLAY or request.evidence_kind != "synthetic_fixture":
            reasons.append("REPLAY_ONLY")
        if intent.side is not Side.BUY or intent.purpose != "entry":
            reasons.append("ENTRY_ONLY")
        if any(v is not True for v in (e.account_scope_verified, e.trade_permission_verified,
                e.market_approved, e.event_time_verified, e.order_coverage_complete)):
            reasons.append("UNVERIFIED_SCOPE_PERMISSION_MARKET_OR_COVERAGE")
        if (intent.symbol, intent.base_asset, intent.quote_asset) != (e.market_symbol, e.base_asset, e.quote_asset):
            reasons.append("MARKET_IDENTITY_MISMATCH")
        age = (now-utc(e.observed_at)).total_seconds()
        if type(e.max_age_seconds) is not int or not 0 <= e.max_age_seconds <= 60 or not 0 <= age <= e.max_age_seconds:
            reasons.append("STALE_OR_FUTURE_QUOTE")
        values = (e.bid, e.ask, e.price_step, e.quantity_step, e.min_notional, e.max_notional)
        if any(type(v) is not Decimal or not v.is_finite() or v <= 0 or len(v.as_tuple().digits) > 100
               or abs(v.as_tuple().exponent) > 200 for v in values):
            raise ValueError()
        if (type(e.fee_quote_rate) is not Decimal or not e.fee_quote_rate.is_finite()
                or len(e.fee_quote_rate.as_tuple().digits) > 100 or abs(e.fee_quote_rate.as_tuple().exponent) > 200
                or not 0 <= e.fee_quote_rate < 1 or e.bid >= e.ask or e.min_notional > e.max_notional):
            raise ValueError()
        if (type(e.opposing_sell_prices) is not tuple or len(e.opposing_sell_prices) > 100
                or any(type(p) is not Decimal or not p.is_finite() or p <= 0 for p in e.opposing_sell_prices)):
            raise ValueError()
        if any(p <= intent.price_quote_per_base for p in e.opposing_sell_prices):
            reasons.append("SELF_TRADE_RISK")
        price, quantity = Fraction(intent.price_quote_per_base), Fraction(intent.quantity_base)
        if (price/Fraction(e.price_step)).denominator != 1 or (quantity/Fraction(e.quantity_step)).denominator != 1:
            reasons.append("INVALID_INSTRUMENT_INCREMENT")
        if not Fraction(e.min_notional) <= price*quantity <= Fraction(e.max_notional):
            reasons.append("NOTIONAL_LIMIT")
        if type(e.risk_inputs) is not PipelineInputs:
            raise ValueError()
        risk = assess_risk_pipeline(e.risk_inputs)
        r = e.risk_inputs
        if not risk.local_conditions_satisfied or risk.sizing is None or not risk.sizing.calculation_valid:
            reasons.append("RISK_PIPELINE_BLOCKED")
        else:
            if quantity > Fraction(risk.sizing.theoretical_quantity_upper_bound):
                reasons.append("RISK_SIZE_EXCEEDED")
        if (r.account_id != intent.account_id or r.quote_asset != intent.quote_asset
                or r.frame.symbol != intent.symbol or r.frame.base_asset != intent.base_asset
                or r.frame.as_of != int(e.observed_at.timestamp()) or r.frame.as_of != int(now.timestamp())):
            reasons.append("ACCOUNT_OR_CUTOFF_MISMATCH")
        if type(r.pe) is not Decimal or not r.pe.is_finite() or price > Fraction(r.pe) or Fraction(e.ask) > Fraction(r.pe):
            reasons.append("PE_LIMIT")
        funded = price*quantity*(1+Fraction(e.fee_quote_rate))
        if (type(r.free_quote) is not Decimal or not r.free_quote.is_finite()
                or funded > Fraction(r.free_quote) or Fraction(request.reservation.amount) < funded
                or request.reservation.capacity_limit > r.free_quote):
            reasons.append("UPFRONT_FEE_OR_CAPITAL_SHORTFALL")
    except (ValueError, TypeError, AttributeError, ArithmeticError, OverflowError):
        reasons.append("INVALID_ADMISSION_EVIDENCE")
    return AdmissionAssessment(not reasons, tuple(dict.fromkeys(reasons)) or ("SYNTHETIC_LOCAL_CHECKS_PASSED",))


def assess_protective_exit(ledger, entry_intent_id, *, stop, bid, observed_at, now, max_age_seconds):
    """Watch confirmed replay exposure, not a live stop or a guaranteed sale.

    No exit price/quantity is invented. A triggered stop explicitly blocks on the
    absent reviewed SELL transport. Partial fills and base fees come from Ledger.
    """
    result = {"action": "NO_TRADE", "executable_ready": False, "stop_triggered": None,
              "settlement": "UNVERIFIED_NOT_AN_ACCOUNT_BALANCE", "reasons": []}
    try:
        record = ledger.get(entry_intent_id)
        request = record.request
        if request.intent.mode is not Mode.REPLAY or request.intent.side is not Side.BUY:
            raise ValueError()
        if (type(stop) is not Decimal or type(bid) is not Decimal or not stop.is_finite() or not bid.is_finite()
                or not 0 < stop < request.intent.price_quote_per_base or bid <= 0
                or type(max_age_seconds) is not int or not 0 <= max_age_seconds <= 60
                or not 0 <= (utc(now)-utc(observed_at)).total_seconds() <= max_age_seconds):
            raise ValueError()
        fills = ledger.fills(entry_intent_id)
        if not fills or any(f.fee_amount is None or f.fee_asset not in (request.intent.base_asset, request.intent.quote_asset) for f in fills):
            raise ValueError()
        net = sum((Fraction(f.quantity_base) - (Fraction(f.fee_amount) if f.fee_asset == request.intent.base_asset else 0) for f in fills), Fraction(0))
        result["stop_triggered"] = bid <= stop
        result["confirmed_net_base_fraction"] = str(net)
        result["quote_asset"] = request.intent.quote_asset
        result["reasons"] = ["EXIT_TRANSPORT_AND_SETTLEMENT_UNIMPLEMENTED" if result["stop_triggered"] else "STOP_NOT_TRIGGERED"]
        if record.reservation_held:
            result["reasons"].append("ENTRY_RECONCILIATION_OR_CANCELLATION_REQUIRED")
    except (ValueError, TypeError, AttributeError, ArithmeticError):
        result["reasons"] = ["EXIT_EVIDENCE_UNAVAILABLE_OR_INVALID"]
    return result
