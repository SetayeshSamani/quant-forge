"""Runnable SYNTHETIC offline TP-1 demo; no real account evidence or live defaults."""

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from wallex.infra.credit_account import (
    AccountKind, CreditAccountBinding, CreditEvidenceInputs, CreditResponse, CreditSemantics,
)
from wallex.market.datasets import load_dataset
from wallex.market.evaluation import EvidenceKind, canonical_json
from wallex.market.normalization import Timeframe, TimestampUnit
from wallex.market.replay import ReplayPlan
from wallex.risk.loss_guards import LossGuardState, loss_policy_for
from wallex.risk.pipeline import PipelineInputs
from wallex.risk.portfolio_guards import PortfolioGuardState
from wallex.risk.sizing import RiskProfile
from .tp1 import run_tp1_shadow

# Demo assumptions only: independent budgets, never projections of wallet/debt.
SYNTHETIC_EQUITY = Decimal("10000")
SYNTHETIC_FREE_QUOTE = Decimal("2000")
SYNTHETIC_PE = Decimal("110")
SYNTHETIC_EXTRA_LOSS = Decimal("0.1")


def synthetic_plan():
    fixtures = Path(__file__).resolve().parents[1] / "backtest" / "fixtures"
    settings = dict(symbol="SYNTHETIC-BASE-TMN", as_of=910800,
                    timestamp_unit=TimestampUnit.SECONDS)
    m15 = load_dataset(fixtures / "synthetic_m15.jsonl", timeframe=Timeframe.M15, **settings)
    h1 = load_dataset(fixtures / "synthetic_h1.jsonl", timeframe=Timeframe.H1, **settings)
    return ReplayPlan(m15, h1, "BASE", "TMN", 900000, 910800)


def synthetic_risk_inputs(frame, binding):
    cutoff, observed = frame.as_of, frame.as_of - 2
    return PipelineInputs(
        frame=frame, pe=SYNTHETIC_PE, equity=SYNTHETIC_EQUITY, free_quote=SYNTHETIC_FREE_QUOTE,
        worst_case_extra_loss_per_unit=SYNTHETIC_EXTRA_LOSS,
        quote_asset=binding.currency, account_id=binding.sub_account_client_id, profile=RiskProfile.L0,
        loss_state=LossGuardState(
            current_equity=SYNTHETIC_EQUITY, day_start_equity=SYNTHETIC_EQUITY,
            high_water_equity=SYNTHETIC_EQUITY, quote_asset=binding.currency, as_of=cutoff,
            day_start_at=cutoff // 86400 * 86400, equity_observed_at=observed,
            policy=loss_policy_for(RiskProfile.L0), prior_daily_breach=False, prior_drawdown_breach=False),
        portfolio_state=PortfolioGuardState(
            account_id=binding.sub_account_client_id, quote_asset=binding.currency, as_of=cutoff,
            snapshot_observed_at=observed, max_snapshot_age_seconds=30,
            open_position_count=0, outstanding_order_count=0, uncertain_order_count=0,
            held_reservation_count=0, unsettled_fill_count=0, positions_complete=True,
            orders_complete=True, reservations_complete=True, settlement_complete=True,
            account_scope_complete=True),
    )


def synthetic_credit_inputs(frame, template):
    # Only the fixed synthetic fixture is rebased; never refresh real observations.
    plan = dict(template.plan.payload)
    result = dict(plan["result"])
    active = dict(result["userPackageDetail"])
    day_start = frame.as_of // 86400 * 86400
    active.update(createdAt=datetime.fromtimestamp(day_start, timezone.utc).isoformat(),
                  expireAt=datetime.fromtimestamp(day_start + 86400, timezone.utc).isoformat())
    result["userPackageDetail"] = active
    plan["result"] = result
    return replace(template, as_of=frame.as_of,
                   accounts=replace(template.accounts, observed_at=frame.as_of - 3),
                   plan=replace(template.plan, payload=plan, observed_at=frame.as_of - 2),
                   balances=replace(template.balances, observed_at=frame.as_of - 1))


def run_demo():
    path = Path(__file__).resolve().parents[1] / "infra" / "fixtures" / "credit_account_synthetic.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("fixture_kind") != "SANITIZED_SYNTHETIC_API_EVIDENCE":
        raise ValueError("Only the bundled synthetic fixture is supported")
    binding = CreditAccountBinding(**(raw["binding"] | {"kind": AccountKind(raw["binding"]["kind"])}))
    semantics = CreditSemantics(**(raw["semantics"] | {
        "balance_units": tuple(tuple(pair) for pair in raw["semantics"]["balance_units"])}))
    template = CreditEvidenceInputs(
        binding, *(CreditResponse(**raw[name]) for name in ("accounts", "plan", "balances")),
        semantics, raw["as_of"], raw["max_age_seconds"])
    result = run_tp1_shadow(synthetic_plan(), strategy_version="tp1-synthetic-account-demo-v1",
        evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE,
        risk_inputs_for_step=lambda frame: synthetic_risk_inputs(frame, binding),
        credit_inputs_for_step=lambda frame: synthetic_credit_inputs(frame, template))
    rows = []
    for record in result.evaluations:
        technical = json.loads(record.decision.reason) if record.decision is not None else None
        detail = json.loads(record.decision.diagnostics_json) if record.decision is not None else None
        blockers = [record.error_code] if record.error_code else []
        if detail is not None:
            blockers += [r for r in technical["reasons"]["frame"]
                         if r not in ("FRAME_VALIDATED", "TECHNICAL_CONDITIONS_MET", "PE_PROVISIONAL")]
            blockers += detail["input_reasons"] + detail["context_reasons"] + detail["limitations"]
            for ready, key in (("risk_local_conditions_satisfied", "risk_reasons"),
                               ("credit_evidence_ready", "credit_reasons")):
                if not detail[ready]:
                    blockers += detail[key]
            blockers.append("APPROVED_RISK_UNAVAILABLE")
        rows.append({"as_of": record.as_of, "evidence_kind": "synthetic_fixture",
                     "action": record.decision.action.value if record.decision is not None else None,
                     "technical": technical, "risk_credit": detail,
                     "blocking_reasons": list(dict.fromkeys(blockers))})
    return {
        "mode": "OFFLINE_TP1_SHADOW_DEMO", "evidence_kind": "synthetic_fixture", "run_id": result.run_id,
        "synthetic_assumptions": {
            "market_units": "UNLABELLED_SYNTHETIC_OHLC_AS_TMN_NO_FX", "quote_asset": "TMN",
            "equity": SYNTHETIC_EQUITY, "free_quote": SYNTHETIC_FREE_QUOTE, "pe": SYNTHETIC_PE,
            "worst_case_extra_loss_per_unit": SYNTHETIC_EXTRA_LOSS, "risk_profile": "L0",
            "equity_origin": "INDEPENDENT_SYNTHETIC_ASSUMPTION_NOT_WALLET_BALANCE",
            "account_state": "SYNTHETIC_FLAT_COMPLETE_NO_PRIOR_BREACHES",
            "credit_plan": "SYNTHETIC_REPLAY_UTC_DAY_ONLY_UNAPPROVED_SEMANTICS",
            "observation_offsets_seconds": {"accounts": -3, "plan": -2, "balances": -1,
                                            "equity": -2, "portfolio": -2},
            "maximum_evidence_age_seconds": 30,
        },
        "summary": {
            "steps": result.summary.step_count, "errors": result.summary.error_count,
            "no_trade": sum(row["action"] == "NO_TRADE" for row in rows),
            "technical_eligible": sum(bool(row["technical"] and row["technical"]["technical_eligible"]) for row in rows),
            "risk_local_conditions_satisfied": sum(bool(row["risk_credit"] and row["risk_credit"]["risk_local_conditions_satisfied"]) for row in rows),
            "credit_evidence_ready": sum(bool(row["risk_credit"] and row["risk_credit"]["credit_evidence_ready"]) for row in rows),
            "blocked": sum(bool(row["blocking_reasons"]) for row in rows), "executable_ready": False,
            "blocking_reason_counts": dict(Counter(reason for row in rows for reason in row["blocking_reasons"])),
        },
        "steps": rows,
    }


def main():
    print(canonical_json(run_demo()))


if __name__ == "__main__":
    main()
