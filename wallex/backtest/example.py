"""Synthetic harness demonstration, not TP-1 or a performance recommendation."""

import argparse
from decimal import Decimal
import json
from pathlib import Path

from wallex.market.datasets import load_dataset
from wallex.market.evaluation import Action, EvidenceKind, ReplayDecision, canonical_json, json_value
from wallex.market.normalization import Timeframe, TimestampUnit
from wallex.market.replay import ReplayPlan
from .engine import SimulationConfig, run_backtest


def example_plan() -> ReplayPlan:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    settings = dict(symbol="SYNTHETIC-BASE-QUOTE", as_of=910800,
                    timestamp_unit=TimestampUnit.SECONDS)
    m15 = load_dataset(fixtures / "synthetic_m15.jsonl", timeframe=Timeframe.M15, **settings)
    h1 = load_dataset(fixtures / "synthetic_h1.jsonl", timeframe=Timeframe.H1, **settings)
    return ReplayPlan(m15, h1, "BASE", "QUOTE", 900000, 910800)


def synthetic_decision(frame) -> ReplayDecision:
    """Two predeclared fixture actions; NOT an implementation of team strategy."""
    if frame.as_of == 900000:
        return ReplayDecision(Action.BUY, "synthetic fixture entry", Decimal(1))
    if frame.as_of == 903600:
        return ReplayDecision(Action.SELL, "synthetic fixture exit", Decimal(1))
    return ReplayDecision(Action.NO_TRADE, "synthetic fixture idle")


def main() -> None:
    from wallex.shadow import compare_runs, run_shadow

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="NEW directory for deterministic example artifacts")
    args = parser.parse_args()
    plan = example_plan()
    settings = dict(strategy_version="synthetic-two-actions-v1",
                    evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
    # Explicit fixture assumptions only; not defaults for any real account.
    result = run_backtest(plan, synthetic_decision, config=SimulationConfig(
        initial_cash_quote=Decimal(1000), fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.002"), latency_seconds=0,
    ), **settings)
    shadow = run_shadow(plan, synthetic_decision, **settings)
    comparison = compare_runs(result, shadow)
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=False)
        result.export(args.output / "backtest")
        shadow.export(args.output / "shadow")
        with (args.output / "comparison.json").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(comparison) + "\n")
    print(json.dumps(json_value({"evidence_kind": settings["evidence_kind"],
        "run_id": result.run_id, "backtest": result.summary, "shadow": shadow.summary,
        "comparison": comparison}), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
