"""Read-only integration with teammate diagnostics when the newer main is present."""

from dataclasses import fields
from decimal import Decimal
from importlib.util import find_spec
from types import SimpleNamespace
import unittest

from wallex.backtest.example import example_plan
from wallex.market.frame_example import build_synthetic_frame


@unittest.skipUnless(find_spec("wallex.strategy.frame_adapter") is not None,
                     "newer main's provisional Strategy diagnostic adapter is not in this base branch")
class StrategyIntegrationTests(unittest.TestCase):
    def test_actual_market_frame_is_accepted_with_exact_projection_and_no_authorization(self):
        from wallex.strategy.frame_adapter import assess_market_frame

        frame = build_synthetic_frame()
        result = assess_market_frame(frame)
        self.assertTrue(result.frame_valid, result.reasons)
        self.assertFalse(result.executable_ready)
        self.assertEqual(result.executable_action.value, "NO_TRADE")
        self.assertIsNone(result.stop)
        self.assertEqual(result.metadata.as_of, frame.as_of)
        self.assertEqual(result.metadata.m15.dataset_sha256, frame.dataset_sha256_15m)
        self.assertEqual(result.metadata.h1.dataset_sha256, frame.dataset_sha256_1h)
        for source, projected in ((frame.candles_15m, result.metadata.m15),
                                  (frame.candles_1h, result.metadata.h1)):
            self.assertEqual(projected.volumes_base, tuple(c.volume for c in source))
            self.assertEqual(tuple(c.close for c in projected.candles), tuple(c.close for c in source))
            self.assertEqual(tuple(c.timestamp for c in projected.candles), tuple(c.timestamp for c in source))

    def test_causal_replay_frames_match_strategy_freshness_at_intermediate_hours(self):
        from wallex.strategy.frame_adapter import assess_market_frame

        for step in example_plan().steps():
            with self.subTest(as_of=step.as_of):
                result = assess_market_frame(step.frame)
                self.assertTrue(result.frame_valid, result.reasons)
                self.assertEqual(result.trend.latest_closed_at, step.as_of // 3600 * 3600)
                self.assertEqual(result.pullback.decision_closed_at, step.as_of)
                self.assertFalse(result.executable_ready)
                self.assertEqual(result.executable_action.value, "NO_TRADE")

    def test_missing_evidence_cannot_be_upgraded_by_populating_provisional_quote(self):
        from wallex.strategy.frame_adapter import assess_market_frame

        frame = build_synthetic_frame()
        data = {field.name: getattr(frame, field.name) for field in fields(frame)}
        forged = SimpleNamespace(**(data | {"best_bid": Decimal(100)}))
        result = assess_market_frame(forged)
        self.assertFalse(result.frame_valid)
        self.assertIsNone(result.metadata)
        self.assertFalse(result.executable_ready)
        self.assertIn("INVALID_MARKET_EVIDENCE", tuple(r.value for r in result.reasons))


if __name__ == "__main__":
    unittest.main()
