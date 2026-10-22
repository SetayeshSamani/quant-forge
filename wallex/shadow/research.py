"""Post-process actual TP-1 Shadow diagnostics; never an order or backtest engine."""

from collections import Counter
import json

from wallex.market.evaluation import Action
from .runner import ShadowResult


def evaluate_candidates(shadow: ShadowResult) -> dict:
    """Use run_tp1_shadow results for any synthetic/historical offline ReplayPlan.

    Indicators/closed-candle validation are reused, not recalculated here. Missing
    Pe leaves stop validity unknown; Trend AND Pullback can still be a technical
    candidate. No entry, exit, quantity, fill or profitability claim is generated.
    """
    if type(shadow) is not ShadowResult:
        raise ValueError("ShadowResult required")
    metadata = json.loads(shadow.metadata_json)
    rejected = Counter()
    candidates = []
    for record in shadow.evaluations:
        if record.error_code or record.decision is None:
            rejected["replay:" + (record.error_code or "NO_DECISION")] += 1
            continue
        decision = record.decision
        if decision.action is not Action.NO_TRADE or decision.quantity_base is not None:
            rejected["research:NON_DIAGNOSTIC_DECISION"] += 1
            continue
        try:
            technical = json.loads(decision.reason)
            details = json.loads(decision.diagnostics_json or "{}")
            evidence = details["technical_evidence"]
            if not technical["frame_valid"]:
                rejected.update("frame:" + code for code in technical["reasons"]["frame"]
                                if code != "DIAGNOSTIC_ONLY")
                continue
            trend, pullback, stop = (evidence[name] for name in ("trend", "pullback", "stop"))
            failures = [name + ":" + code for name, component in (("trend", trend), ("pullback", pullback))
                        if component["eligible"] is not True for code in component["reasons"]]
            if failures:
                rejected.update(failures)
                continue
            if (trend["eligible"] is not True or pullback["eligible"] is not True
                    or not evidence["as_of"] == pullback["decision_closed_at"] == record.as_of
                    or trend["latest_closed_at"] != record.as_of // 3600 * 3600
                    or (stop is not None and stop["valid"] and stop["decision_closed_at"] != record.as_of)):
                rejected["research:EVIDENCE_CUTOFF_MISMATCH"] += 1
                continue
            candidates.append({
                "as_of": record.as_of, "technical_candidate": True,
                "evidence": evidence, "stop_valid": None if stop is None else stop["valid"],
                "frame_reasons": technical["reasons"]["frame"],
                "risk_reasons": details["risk_reasons"], "credit_reasons": details["credit_reasons"],
                "limitations": details["limitations"],
                "action": "NO_TRADE", "executable_ready": False, "execution_approved": False,
            })
        except (KeyError, TypeError, ValueError):
            rejected["research:TECHNICAL_EVIDENCE_UNAVAILABLE"] += 1
    return {
        "mode": "TP1_RESEARCH_ONLY", "run_id": shadow.run_id,
        "evidence_kind": metadata["evidence_kind"], "symbol": metadata["symbol"],
        "quote_asset": metadata["quote_asset"],
        "window": [metadata["start_as_of"], metadata["end_as_of"]],
        "steps": len(shadow.evaluations), "candidate_count": len(candidates),
        "candidate_timestamps": [row["as_of"] for row in candidates], "candidates": candidates,
        "rejected_step_count": len(shadow.evaluations) - len(candidates),
        "technical_rejection_reason_counts": dict(sorted(rejected.items())),
        "action": "NO_TRADE", "executable_ready": False,
    }
