"""Deterministic single-instrument OHLC simulation, never exchange execution."""

from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, Underflow
from fractions import Fraction
import json

from wallex.market.evaluation import (
    Action, DecisionProvider, EvaluationRecord, EvidenceKind, Journal, ReplayDecision,
    RunEvent, evaluate_step, run_metadata, write_artifacts,
)
from wallex.market.replay import ReplayPlan


def _decimal(value: Fraction) -> Decimal:
    # Only reporting rounds. Ledger operations and balance admission are exact
    # rational arithmetic, independent of the caller's Decimal context.
    context = Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999, flags=[],
                      traps=[InvalidOperation, DivisionByZero, Overflow, Underflow])
    return context.divide(Decimal(value.numerator), Decimal(value.denominator))


@dataclass(frozen=True)
class SimulationConfig:
    initial_cash_quote: Decimal
    fee_rate: Decimal
    slippage_rate: Decimal
    latency_seconds: int

    def __post_init__(self) -> None:
        for name in ("initial_cash_quote", "fee_rate", "slippage_rate"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.initial_cash_quote <= 0:
            raise ValueError("initial_cash_quote must be positive")
        if not 0 <= self.fee_rate < 1 or not 0 <= self.slippage_rate < 1:
            raise ValueError("fee/slippage must be explicit fractional rates in [0, 1)")
        if type(self.latency_seconds) is not int or self.latency_seconds < 0:
            raise ValueError("latency_seconds must be an explicit nonnegative integer")


@dataclass(frozen=True)
class SimulatedFill:
    """Hypothetical next-open fill, NOT teammate ExecutionReceipt/Fill evidence."""

    run_id: str
    mode: str
    evidence_kind: str
    decision_as_of: int
    filled_at: int
    action: Action
    quantity_base: Decimal
    reference_open: Decimal
    price_quote_per_base: Decimal
    fee_quote: Decimal
    slippage_cost_quote: Decimal
    cash_change_quote: Decimal
    realized_pnl_quote: Decimal | None


@dataclass(frozen=True)
class EquityPoint:
    as_of: int
    cash_quote: Decimal
    quantity_base: Decimal
    mark_price: Decimal
    equity_quote: Decimal
    drawdown_fraction: Decimal


@dataclass(frozen=True)
class BacktestSummary:
    initial_cash_quote: Decimal
    ending_cash_quote: Decimal
    ending_quantity_base: Decimal
    ending_equity_quote: Decimal
    net_pnl_quote: Decimal
    net_return_fraction: Decimal
    realized_pnl_quote: Decimal
    unrealized_pnl_quote: Decimal
    max_drawdown_fraction: Decimal
    fees_quote: Decimal
    slippage_cost_quote: Decimal
    fill_count: int
    entry_count: int
    completed_exit_count: int
    rejected_count: int
    evaluation_error_count: int
    pending_at_end: bool


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    metadata_json: str
    summary: BacktestSummary
    evaluations: tuple[EvaluationRecord, ...]
    fills: tuple[SimulatedFill, ...]
    equity: tuple[EquityPoint, ...]
    events: tuple[RunEvent, ...]

    def export(self, directory):
        return write_artifacts(directory, metadata_json=self.metadata_json,
                               summary=self.summary, events=self.events,
                               tables=(("fills.jsonl", self.fills), ("equity.jsonl", self.equity),
                                       ("evaluations.jsonl", self.evaluations)))


def run_backtest(
    plan: ReplayPlan, provider: DecisionProvider, *, config: SimulationConfig,
    strategy_version: str, evidence_kind: EvidenceKind,
) -> BacktestResult:
    """Single-asset long-only, full fills at a later eligible bar OPEN.

    All costs/timing/capital are mandatory. No leverage, shorts, matching engine,
    stop/limit assumptions, tick rounding, liquidity or actual exchange receipt.
    latency=0 still uses the next bar after the decision's completed candle.
    Positions and pending decisions remain open at the end; no forced liquidation.
    """
    if not callable(provider) or not isinstance(config, SimulationConfig):
        raise ValueError("callable provider and explicit SimulationConfig required")
    # Reconstruct config to validate even a hand-built/subclassed instance.
    config = SimulationConfig(config.initial_cash_quote, config.fee_rate,
                              config.slippage_rate, config.latency_seconds)
    metadata = run_metadata(plan, mode="backtest", evidence_kind=evidence_kind,
                            strategy_version=strategy_version, assumptions={
                                "model": "single-asset-next-open-v1", "config": config,
                                "fees": "quote_asset_only", "liquidity": "unlimited_full_fill",
                                "ledger_arithmetic": "exact_fraction", "report_precision": 50,
                                "drawdown": "M15_close_marks", "force_liquidation": False,
                            })
    journal = Journal(metadata)
    journal.add(plan.start_as_of, "run_started", {"metadata": json.loads(metadata)})
    initial = Fraction(config.initial_cash_quote)
    cash, quantity, basis = initial, Fraction(0), Fraction(0)
    fees, slippage_cost, realized = Fraction(0), Fraction(0), Fraction(0)
    peak, max_drawdown = initial, Fraction(0)
    fee_rate, slip_rate = Fraction(config.fee_rate), Fraction(config.slippage_rate)
    pending: tuple[int, ReplayDecision] | None = None
    fills, equity, evaluations = [], [], []
    rejected = 0
    bars = {c.timestamp + 900: c for c in plan.m15.candles}

    for step in plan.steps():
        bar = bars[step.as_of]
        if pending is not None and bar.timestamp >= pending[0] + config.latency_seconds:
            decision_time, decision = pending
            q = Fraction(decision.quantity_base)
            reference = Fraction(bar.open)
            price = reference * (1 + slip_rate if decision.action is Action.BUY else 1 - slip_rate)
            gross = q * price
            fee = gross * fee_rate
            slip = q * abs(price - reference)
            pnl = None
            if decision.action is Action.BUY and gross + fee > cash:
                rejection = "INSUFFICIENT_SIMULATED_CASH"
            elif decision.action is Action.SELL and q > quantity:
                rejection = "INSUFFICIENT_SIMULATED_POSITION"
            else:
                rejection = None
                if decision.action is Action.BUY:
                    cash_change = -(gross + fee)
                    basis += gross + fee
                    quantity += q
                else:
                    allocated_cost = basis * q / quantity
                    cash_change = gross - fee
                    pnl = cash_change - allocated_cost
                    realized += pnl
                    basis -= allocated_cost
                    quantity -= q
                cash += cash_change
                fees += fee
                slippage_cost += slip
                fill = SimulatedFill(
                    journal.run_id, "backtest", evidence_kind.value,
                    decision_time, bar.timestamp, decision.action, _decimal(q),
                    bar.open, _decimal(price), _decimal(fee), _decimal(slip),
                    _decimal(cash_change), None if pnl is None else _decimal(pnl),
                )
                fills.append(fill)
                journal.add(bar.timestamp, "simulated_fill", fill)
            if rejection:
                rejected += 1
                journal.add(bar.timestamp, "simulation_rejected", {
                    "reason": rejection, "decision_as_of": decision_time, "decision": decision,
                })
            pending = None

        mark = Fraction(bar.close)
        value = cash + quantity * mark
        peak = max(peak, value)
        drawdown = (peak - value) / peak
        max_drawdown = max(max_drawdown, drawdown)
        point = EquityPoint(step.as_of, _decimal(cash), _decimal(quantity), bar.close,
                            _decimal(value), _decimal(drawdown))
        equity.append(point)
        journal.add(step.as_of, "equity_mark", point)

        evaluation = evaluate_step(step, provider)
        evaluations.append(evaluation)
        journal.add(step.as_of, "evaluation", evaluation)
        decision = evaluation.decision
        if decision is not None and decision.action is not Action.NO_TRADE:
            if pending is not None:
                rejected += 1
                journal.add(step.as_of, "simulation_rejected", {
                    "reason": "PENDING_DECISION_EXISTS", "decision": decision,
                })
            else:
                pending = (step.as_of, decision)
                journal.add(step.as_of, "simulation_queued", {"decision": decision,
                    "earliest_open_at": step.as_of + config.latency_seconds})

    if pending is not None:
        journal.add(plan.end_as_of, "simulation_unfilled", {
            "reason": "END_OF_REPLAY_DATA", "decision_as_of": pending[0], "decision": pending[1],
        })
    summary = BacktestSummary(
        config.initial_cash_quote, _decimal(cash), _decimal(quantity), _decimal(value),
        _decimal(value - initial), _decimal((value - initial) / initial), _decimal(realized),
        _decimal(quantity * mark - basis), _decimal(max_drawdown), _decimal(fees),
        _decimal(slippage_cost), len(fills), sum(f.action is Action.BUY for f in fills),
        sum(f.action is Action.SELL for f in fills), rejected,
        sum(e.error_code is not None for e in evaluations), pending is not None,
    )
    journal.add(plan.end_as_of, "run_completed", summary)
    return BacktestResult(journal.run_id, metadata, summary, tuple(evaluations),
                          tuple(fills), tuple(equity), tuple(journal.events))
