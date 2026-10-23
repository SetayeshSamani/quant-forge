"""TP-1 diagnostic provider for the existing offline shadow runner; never orders."""

from collections.abc import Callable
from decimal import Decimal
from functools import partial

from wallex.market.evaluation import Action, EvidenceKind, ReplayDecision, canonical_json
from wallex.market.replay import ReplayPlan
from wallex.strategy.frame_adapter import assess_market_frame
from wallex.infra.credit_account import CreditEvidenceInputs
from wallex.risk.credit_pipeline import assess_credit_risk_diagnostics
from wallex.risk.pipeline import PipelineInputs
from .runner import ShadowResult, run_shadow


def _step_input(provider, frame, expected, label, issues):
    if provider is None:
        issues.append(label + "_MISSING")
        return None
    try:
        value = provider(frame)
    except Exception:
        issues.append(label + "_PROVIDER_FAILED")
        return None
    if type(value) is not expected:
        issues.append(label + ("_MISSING" if value is None else "_INVALID"))
        return None
    return value


def _step_context(frame, pe, risk, credit):
    issues = []
    if risk is not None:
        if risk.frame is not frame:
            issues.append("RISK_FRAME_MISMATCH")
        if pe is not None and not (type(pe) is Decimal and pe.is_finite()
                and type(risk.pe) is Decimal and risk.pe.is_finite() and pe == risk.pe):
            issues.append("PE_MISMATCH")
    if credit is not None:
        try:
            if credit.as_of != frame.as_of:
                issues.append("CREDIT_CUTOFF_MISMATCH")
            if credit.binding.currency != frame.quote_asset:
                issues.append("CREDIT_QUOTE_MISMATCH")
            if risk is not None and credit.binding.sub_account_client_id != risk.account_id:
                issues.append("CREDIT_ACCOUNT_MISMATCH")
        except Exception:
            issues.append("CREDIT_CONTEXT_INVALID")
    return issues


def tp1_decision(frame, *, pe: Decimal | None = None,
                 risk_inputs_for_step: Callable[[object], PipelineInputs | None] | None = None,
                 credit_inputs_for_step: Callable[[object], CreditEvidenceInputs | None] | None = None,
                 adviser=None,
                 ) -> ReplayDecision:
    """Journal actual technical reasons; Pe stays explicit and provisional.

    Technical eligibility does not establish approved market/risk evidence. No
    risk approval interface or executable action is provided by this adapter.
    """
    issues = []
    risk = _step_input(risk_inputs_for_step, frame, PipelineInputs, "RISK_INPUTS", issues)
    credit = _step_input(credit_inputs_for_step, frame, CreditEvidenceInputs, "CREDIT_EVIDENCE", issues)
    context = _step_context(frame, pe, risk, credit)
    # Inconsistent step evidence must not reach theoretical sizing.
    if context:
        risk = None
    diagnostics = assess_credit_risk_diagnostics(credit, risk)
    assessed_risk = diagnostics.risk
    assessment = assessed_risk.frame
    if assessment is None:
        assessment = assess_market_frame(frame, pe=risk.pe if risk is not None else pe)
    risk_details = {
        "technical_evidence": {
            "as_of": assessment.metadata.as_of if assessment.metadata is not None else None,
            "trend": assessment.trend, "pullback": assessment.pullback, "stop": assessment.stop,
        },
        "input_reasons": issues, "context_reasons": context,
        "risk_reasons": assessed_risk.reasons,
        "credit_reasons": diagnostics.credit.reasons,
        "credit_evidence_ready": diagnostics.credit_evidence_ready and not context,
        "risk_local_conditions_satisfied": assessed_risk.local_conditions_satisfied and not context,
        "blocks_new_entries": True, "executable_ready": False,
        "limitations": diagnostics.limitations + diagnostics.credit.limitations + assessed_risk.limitations,
    }
    for name in ("loss", "portfolio", "sizing"):
        component = getattr(assessed_risk, name)
        risk_details[name + "_reasons"] = None if component is None else component.reasons
    if adviser is not None:
        try:
            risk_details["ai"] = adviser(frame, assessment)
        except Exception:
            risk_details["ai"] = {"status": "ADVISER_FAILED", "action": "NO_TRADE",
                                  "executable_ready": False}
    reasons = {"frame": assessment.reasons}
    for name in ("trend", "pullback", "stop"):
        component = getattr(assessment, name)
        reasons[name] = None if component is None else component.reasons
    return ReplayDecision(Action.NO_TRADE, canonical_json({
        "reasons": reasons,
        "frame_valid": assessment.frame_valid,
        "technical_eligible": assessment.technical_eligible,
        **({"research_candidate_retained": assessment.technical_eligible and risk_details["ai"].get("retained_for_research") is True} if adviser is not None else {}),
        "risk_approval": "UNAVAILABLE",
        "executable_ready": False,
    }), diagnostics_json=canonical_json(risk_details))


def run_tp1_shadow(plan: ReplayPlan, *, strategy_version: str,
                   evidence_kind: EvidenceKind, pe: Decimal | None = None,
                   risk_inputs_for_step: Callable[[object], PipelineInputs | None] | None = None,
                   credit_inputs_for_step: Callable[[object], CreditEvidenceInputs | None] | None = None,
                   adviser=None,
                   ) -> ShadowResult:
    """Reuse run_shadow; caller callbacks must be pure and use the exact step frame.

    Risk Pe/account/equity/costs/states remain caller supplied. A supplied global
    Pe must match step Pe. Credit client ID must equal the explicit risk account ID;
    these provisional local checks are not approved account/risk authorization.
    """
    return run_shadow(plan, partial(tp1_decision, pe=pe,
                                   risk_inputs_for_step=risk_inputs_for_step,
                                   credit_inputs_for_step=credit_inputs_for_step, adviser=adviser),
                      strategy_version=strategy_version, evidence_kind=evidence_kind)
