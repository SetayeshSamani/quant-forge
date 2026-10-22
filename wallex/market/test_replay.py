"""Causal prefixes, data failures and internal offline record contracts."""

from dataclasses import replace
from decimal import Decimal
import unittest

from wallex.backtest.example import example_plan
from wallex.market.evaluation import Action, ReplayDecision, canonical_json, evaluate_step
from wallex.market.frames import MarketFrameError


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.plan = example_plan()

    def test_h1_is_available_only_at_its_actual_close_and_never_resampled(self):
        steps = list(self.plan.steps())
        self.assertEqual(len(steps), 13)
        self.assertEqual(steps[0].as_of, 900000)
        for step in steps:
            self.assertIsNone(step.issue)
            self.assertTrue(all(c.timestamp + 900 <= step.as_of for c in step.frame.candles_15m))
            self.assertTrue(all(c.timestamp + 3600 <= step.as_of for c in step.frame.candles_1h))
        self.assertEqual(steps[1].frame.candles_1h[-1].timestamp + 3600, 900000)
        self.assertEqual(steps[4].frame.candles_1h[-1].timestamp + 3600, 903600)
        self.assertEqual(steps, list(self.plan.steps()))

    def test_future_price_changes_cannot_change_earlier_numeric_inputs(self):
        cutoff = 903600
        candles = tuple(replace(c, open=Decimal(500), high=Decimal(501), low=Decimal(499),
                                close=Decimal(500)) if c.timestamp >= cutoff else c
                        for c in self.plan.m15.candles)
        alternate = replace(self.plan, m15=replace(self.plan.m15, candles=candles, sha256="a" * 64))
        left = [step for step in self.plan.steps() if step.as_of <= cutoff]
        right = [step for step in alternate.steps() if step.as_of <= cutoff]
        for a, b in zip(left, right):
            self.assertEqual(a.frame.candles_15m, b.frame.candles_15m)
            self.assertEqual(a.frame.candles_1h, b.frame.candles_1h)

    def test_warmup_is_reported_per_step_without_calling_the_provider(self):
        start = self.plan.m15.candles[0].timestamp + 900
        plan = replace(self.plan, start_as_of=start)
        calls = []
        def provider(frame):
            calls.append(frame.as_of)
            return ReplayDecision(Action.NO_TRADE, "fixture")
        records = [evaluate_step(step, provider) for step in plan.steps()]
        self.assertTrue(records[0].error_code.startswith("DATA_INSUFFICIENT_HISTORY"))
        self.assertTrue(any(record.error_code for record in records))
        self.assertEqual(min(calls), 900000)
        self.assertEqual(len(calls), 13)
        self.assertEqual(len(records), (plan.end_as_of - start) // 900 + 1)

    def test_invalid_windows_and_unsupported_types_are_rejected(self):
        for changes in ({"start_as_of": True}, {"start_as_of": 900001},
                        {"end_as_of": 910801}, {"end_as_of": 900000.0},
                        {"start_as_of": 911700}, {"start_as_of": 0},
                        {"end_as_of": 911700}, {"m15": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.plan, **changes)

    def test_corrupted_full_source_is_rejected_before_replay(self):
        candles = self.plan.h1.candles
        bad = replace(self.plan.h1, candles=(candles[0],) + candles[2:])
        with self.assertRaises(MarketFrameError):
            replace(self.plan, h1=bad)

    def test_internal_decisions_require_reason_action_and_explicit_quantity(self):
        invalid = (("BUY", "reason", Decimal(1)), (Action.BUY, "", Decimal(1)),
                   (Action.BUY, "reason", None), (Action.BUY, "reason", 1),
                   (Action.BUY, "reason", Decimal("NaN")),
                   (Action.SELL, "reason", Decimal(0)),
                   (Action.NO_TRADE, "reason", Decimal(1)))
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                ReplayDecision(*args)

    def test_export_json_is_explicit_about_financial_strings_and_rejects_float(self):
        text = canonical_json(ReplayDecision(Action.BUY, "fixture", Decimal("0.125")))
        self.assertIn('"quantity_base":"0.125"', text)
        with self.assertRaises(ValueError):
            canonical_json({"price": 0.125})


if __name__ == "__main__":
    unittest.main()
