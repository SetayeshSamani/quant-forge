"""Hand-calculated accounting, causal fills, latency, and reproducible artifacts."""

from dataclasses import replace
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from wallex.market.evaluation import Action, EvidenceKind, ReplayDecision, canonical_json
from .engine import SimulationConfig, run_backtest
from .example import example_plan, synthetic_decision

D = Decimal
START = 900000


def priced_plan(rows):
    """Rows describe (open, close) at successive decision-close timestamps."""
    plan = example_plan()
    overrides = {START + i * 900: (D(str(o)), D(str(c))) for i, (o, c) in enumerate(rows)}
    candles = []
    for c in plan.m15.candles:
        if c.timestamp + 900 in overrides:
            opening, close = overrides[c.timestamp + 900]
            c = replace(c, open=opening, close=close, high=max(opening, close) + D(1),
                        low=min(opening, close) / D(2))
        candles.append(c)
    raw = [{key: getattr(c, key) for key in
            ("timestamp", "open", "high", "low", "close", "volume", "completed")}
           for c in candles]
    payload = "\n".join(canonical_json(row) for row in raw).encode("ascii")
    m15 = replace(plan.m15, candles=tuple(candles), sha256=sha256(payload).hexdigest())
    return replace(plan, m15=m15, end_as_of=START + (len(rows) - 1) * 900)


def actions(mapping):
    return lambda frame: mapping.get(frame.as_of, ReplayDecision(Action.NO_TRADE, "fixture idle"))


class BacktestTests(unittest.TestCase):
    def run_case(self, plan=None, provider=synthetic_decision, **changes):
        values = dict(initial_cash_quote=D(1000), fee_rate=D(0), slippage_rate=D(0),
                      latency_seconds=0)
        config = SimulationConfig(**(values | changes))
        return run_backtest(plan or example_plan(), provider, config=config,
                            strategy_version="fixture-v1", evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)

    def test_flat_round_trip_loses_only_explicit_fees_and_slippage(self):
        result = self.run_case(fee_rate=D("0.001"), slippage_rate=D("0.002"))
        summary = result.summary
        self.assertEqual(summary.ending_cash_quote, D("999.4"))
        self.assertEqual(summary.net_pnl_quote, D("-0.6"))
        self.assertEqual(summary.net_return_fraction, D("-0.0006"))
        self.assertEqual(summary.fees_quote, D("0.2"))
        self.assertEqual(summary.slippage_cost_quote, D("0.4"))
        self.assertEqual(summary.max_drawdown_fraction, D("0.0006"))
        self.assertEqual((summary.fill_count, summary.entry_count, summary.completed_exit_count), (2, 1, 1))
        self.assertEqual([fill.price_quote_per_base for fill in result.fills], [D("100.2"), D("99.8")])

    def test_decisions_fill_on_next_open_never_the_decision_close(self):
        plan = priced_plan([(100, 100), (110, 115), (120, 120)])
        provider = actions({START: ReplayDecision(Action.BUY, "entry", D(1)),
                            START + 900: ReplayDecision(Action.SELL, "exit", D(1))})
        result = self.run_case(plan, provider)
        self.assertEqual([f.price_quote_per_base for f in result.fills], [D(110), D(120)])
        self.assertEqual([f.filled_at for f in result.fills], [START, START + 900])
        self.assertEqual(result.summary.net_pnl_quote, D(10))

    def test_latency_waits_for_first_eligible_future_open(self):
        provider = actions({START: ReplayDecision(Action.BUY, "entry", D(1))})
        immediate = self.run_case(provider=provider, latency_seconds=0)
        delayed = self.run_case(provider=provider, latency_seconds=1)
        self.assertEqual(immediate.fills[0].filled_at, START)
        self.assertEqual(delayed.fills[0].filled_at, START + 900)
        self.assertNotEqual(immediate.run_id, delayed.run_id)

    def test_one_pending_decision_blocks_another_without_replacing_it(self):
        provider = actions({START: ReplayDecision(Action.BUY, "first", D(1)),
                            START + 900: ReplayDecision(Action.BUY, "second", D(2))})
        result = self.run_case(provider=provider, latency_seconds=1800)
        self.assertEqual(result.summary.rejected_count, 1)
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].quantity_base, D(1))
        self.assertEqual(result.fills[0].filled_at, START + 1800)

    def test_insufficient_cash_does_not_resize_quantity_or_charge_fees(self):
        provider = actions({START: ReplayDecision(Action.BUY, "too large", D(100))})
        result = self.run_case(provider=provider, initial_cash_quote=D(100))
        self.assertEqual(result.summary.rejected_count, 1)
        self.assertEqual(result.summary.ending_cash_quote, D(100))
        self.assertEqual(result.summary.ending_quantity_base, D(0))
        self.assertEqual(result.summary.fees_quote, D(0))
        self.assertFalse(result.fills)

    def test_short_selling_and_overselling_are_rejected(self):
        provider = actions({START: ReplayDecision(Action.SELL, "no position", D(1)),
                            START + 900: ReplayDecision(Action.BUY, "entry", D(1)),
                            START + 1800: ReplayDecision(Action.SELL, "oversell", D(2))})
        result = self.run_case(provider=provider)
        self.assertEqual(result.summary.rejected_count, 2)
        self.assertEqual(result.summary.fill_count, 1)
        self.assertEqual(result.summary.ending_quantity_base, D(1))

    def test_partial_exit_allocates_cost_basis_and_keeps_open_position(self):
        plan = priced_plan([(100, 100), (100, 100), (120, 110)])
        provider = actions({START: ReplayDecision(Action.BUY, "entry", D(2)),
                            START + 900: ReplayDecision(Action.SELL, "partial exit", D(1))})
        result = self.run_case(plan, provider)
        self.assertEqual(result.summary.realized_pnl_quote, D(20))
        self.assertEqual(result.summary.unrealized_pnl_quote, D(10))
        self.assertEqual(result.summary.ending_equity_quote, D(1030))
        self.assertEqual(result.summary.ending_quantity_base, D(1))

    def test_multiple_entries_use_weighted_cost_for_partial_exits(self):
        plan = priced_plan([(100, 100), (100, 100), (200, 200), (180, 180)])
        provider = actions({START: ReplayDecision(Action.BUY, "first", D(1)),
                            START + 900: ReplayDecision(Action.BUY, "second", D(1)),
                            START + 1800: ReplayDecision(Action.SELL, "partial", D(1))})
        result = self.run_case(plan, provider)
        self.assertEqual(result.summary.realized_pnl_quote, D(30))
        self.assertEqual(result.summary.unrealized_pnl_quote, D(30))
        self.assertEqual(result.summary.ending_equity_quote, D(1060))

    def test_drawdown_uses_peak_close_equity_and_does_not_force_liquidation(self):
        plan = priced_plan([(100, 100), (100, 80), (100, 120), (100, 90)])
        result = self.run_case(plan, actions({START: ReplayDecision(Action.BUY, "entry", D(1))}),
                               initial_cash_quote=D(100))
        self.assertEqual(result.summary.max_drawdown_fraction, D("0.25"))
        self.assertEqual(result.summary.ending_equity_quote, D(90))
        self.assertEqual(result.summary.realized_pnl_quote, D(0))
        self.assertEqual(result.summary.unrealized_pnl_quote, D(-10))
        self.assertEqual(result.summary.ending_quantity_base, D(1))

    def test_repeating_partial_cost_allocations_do_not_leave_rounding_residue(self):
        plan = priced_plan([(100, 100), (100, 100), (101, 101), (102, 102), (102, 102)])
        provider = actions({START: ReplayDecision(Action.BUY, "first", D(1)),
                            START + 900: ReplayDecision(Action.BUY, "second", D(2)),
                            START + 1800: ReplayDecision(Action.SELL, "partial", D(1)),
                            START + 2700: ReplayDecision(Action.SELL, "remaining", D(2))})
        result = self.run_case(plan, provider)
        self.assertEqual(result.summary.realized_pnl_quote, D(4))
        self.assertEqual(result.summary.unrealized_pnl_quote, D(0))
        self.assertEqual(result.summary.ending_equity_quote, D(1004))
        self.assertEqual(result.summary.ending_quantity_base, D(0))

    def test_end_of_data_leaves_last_decision_unfilled(self):
        plan = replace(example_plan(), end_as_of=START)
        result = self.run_case(plan, actions({START: ReplayDecision(Action.BUY, "last", D(1))}))
        self.assertTrue(result.summary.pending_at_end)
        self.assertEqual(result.summary.fill_count, 0)
        self.assertEqual(result.summary.ending_quantity_base, D(0))
        self.assertTrue(any(e.kind == "simulation_unfilled" for e in result.events))

    def test_ledger_balance_checks_remain_exact_beyond_decimal_context_precision(self):
        opening = D("1.000000000000000000000000000002")
        cash = D("1.000000000000000000000000000001")
        plan = priced_plan([(1, 1), (opening, 1)])
        provider = actions({START: ReplayDecision(Action.BUY, "boundary", D(1))})
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            result = self.run_case(plan, provider, initial_cash_quote=cash)
        self.assertEqual(result.summary.rejected_count, 1)
        self.assertFalse(result.fills)
        self.assertEqual(result.summary.ending_cash_quote, cash)

    def test_results_independent_of_caller_context_and_repeatable(self):
        expected = self.run_case(fee_rate=D("0.001"), slippage_rate=D("0.002"))
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            actual = self.run_case(fee_rate=D("0.001"), slippage_rate=D("0.002"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual.events, expected.events)

    def test_provider_errors_and_bad_returns_are_logged_without_fills(self):
        def fails(frame):
            raise RuntimeError("fixture failure")
        for provider, code in ((fails, "PROVIDER_ERROR"), (lambda frame: {}, "INVALID_DECISION")):
            with self.subTest(code=code):
                result = self.run_case(provider=provider)
                self.assertEqual(result.summary.evaluation_error_count, 13)
                self.assertTrue(all(e.error_code == code for e in result.evaluations))
                self.assertFalse(result.fills)

    def test_missing_or_invalid_simulation_assumptions_are_rejected(self):
        for changes in ({"initial_cash_quote": 1000}, {"initial_cash_quote": D(0)},
                        {"fee_rate": D("NaN")}, {"fee_rate": D(-1)},
                        {"fee_rate": D(1)}, {"slippage_rate": 0.1},
                        {"slippage_rate": D(1)}, {"latency_seconds": True},
                        {"latency_seconds": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_case(**changes)
        with self.assertRaises(TypeError):
            SimulationConfig(initial_cash_quote=D(1000))

    def test_artifacts_are_byte_repeatable_and_existing_destination_is_preserved(self):
        result = self.run_case()
        with TemporaryDirectory() as directory:
            one, two = Path(directory) / "one", Path(directory) / "two"
            result.export(one)
            self.run_case().export(two)
            self.assertEqual({p.name: p.read_bytes() for p in one.iterdir()},
                             {p.name: p.read_bytes() for p in two.iterdir()})
            before = (one / "summary.json").read_bytes()
            with self.assertRaises(FileExistsError):
                result.export(one)
            self.assertEqual((one / "summary.json").read_bytes(), before)
            report = json.loads(before)
            self.assertIsInstance(report["ending_equity_quote"], str)
            self.assertEqual(report["mode"], "backtest")
            self.assertEqual(report["evidence_kind"], "synthetic_fixture")
            for line in (one / "fills.jsonl").read_text().splitlines():
                fill = json.loads(line)
                self.assertEqual(fill["mode"], "backtest")
                self.assertEqual(fill["evidence_kind"], "synthetic_fixture")


if __name__ == "__main__":
    unittest.main()
