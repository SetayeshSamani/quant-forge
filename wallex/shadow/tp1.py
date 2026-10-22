"""TP-1 diagnostic provider for the existing offline shadow runner; never orders."""

from decimal import Decimal
from functools import partial

from wallex.market.evaluation import Action, EvidenceKind, ReplayDecision, canonical_json
from wallex.market.replay import ReplayPlan
from wallex.strategy.frame_adapter import assess_market_frame
from .runner import ShadowResult, run_shadow


def tp1_decision(frame, *, pe: Decimal | None = None) -> ReplayDecision:
    """Journal actual technical reasons; Pe stays explicit and provisional.

    Technical eligibility does not establish approved market/risk evidence. No
    risk approval interface or executable action is provided by this adapter.
    """
    assessment = assess_market_frame(frame, pe=pe)
    reasons = {"frame": assessment.reasons}
    for name in ("trend", "pullback", "stop"):
        component = getattr(assessment, name)
        reasons[name] = None if component is None else component.reasons
    return ReplayDecision(Action.NO_TRADE, canonical_json({
        "reasons": reasons,
        "frame_valid": assessment.frame_valid,
        "technical_eligible": assessment.technical_eligible,
        "risk_approval": "UNAVAILABLE",
        "executable_ready": False,
    }))


def run_tp1_shadow(plan: ReplayPlan, *, strategy_version: str,
                   evidence_kind: EvidenceKind, pe: Decimal | None = None) -> ShadowResult:
    """Delegate all replay, validation and journaling to run_shadow unchanged."""
    return run_shadow(plan, partial(tp1_decision, pe=pe),
                      strategy_version=strategy_version, evidence_kind=evidence_kind)
