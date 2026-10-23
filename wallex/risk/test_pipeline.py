"""Synthetic composition tests; no market/account collection or authorization."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from wallex.strategy.engine import Signal
from wallex.strategy.indicators import Timeframe

from . import gate, loss_guards as loss, pipeline as pipe
from . import portfolio_guards as portfolio, sizing

D = Decimal
R = pipe.PipelineReason
S = pipe.PipelineStage
AS_OF = 1800000


from .fixtures import synthetic_pipeline_inputs as fixture


class PipelineTests(unittest.TestCase):
    def assert_blocked(self, inputs, reason):
        result = pipe.assess_risk_pipeline(inputs)
        self.assertFalse(result.local_conditions_satisfied)
        self.assertFalse(result.executable_ready)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertIn(reason, result.reasons)
        if result.sizing is not None:
            self.assertFalse(result.sizing.calculation_valid)
            self.assertIsNone(result.sizing.theoretical_quantity_upper_bound)
        else:
            self.assertIn(S.SIZING, tuple(s.stage for s in result.skipped_stages))
        return result

    def test_complete_synthetic_profiles_never_authorize_execution(self):
        for profile, expected in ((sizing.RiskProfile.L0, D(".8")),
                                  (sizing.RiskProfile.R0, D(2))):
            with self.subTest(profile=profile):
                result = pipe.assess_risk_pipeline(fixture(profile))
                self.assertTrue(result.coherence_valid)
                self.assertTrue(result.local_conditions_satisfied)
                self.assertEqual(result.reasons, (R.LOCAL_DIAGNOSTIC_CONDITIONS_MET,))
                self.assertEqual(result.sizing.unit_loss, D("12.5"))
                self.assertEqual(result.frame.stop.initial_stop, D("97.6"))
                self.assertEqual(result.sizing.theoretical_quantity_upper_bound, expected)
                self.assertEqual(result.sizing.inputs.pe, D(110))
                self.assertNotEqual(result.sizing.inputs.pe,
                                    result.frame.metadata.m15.candles[-1].close)
                self.assertFalse(result.executable_ready)
                self.assertIs(result.executable_action, Signal.NO_TRADE)
                self.assertIn(R.MARKET_EVIDENCE_UNAPPROVED, result.limitations)
                self.assertIn(R.LOSS_ACCOUNT_PROVENANCE_UNRESOLVED, result.limitations)
                self.assertIn(R.ATOMIC_ADMISSION_UNAVAILABLE, result.limitations)
                self.assertEqual((result.skipped_stages, result.failed_stages), ((), ()))

    def test_technical_rejection_preserves_independent_component_results(self):
        inputs = fixture()
        inputs.frame.candles_15m[-1].close = D(100)
        with patch.object(pipe, "assess_position_size") as size:
            result = self.assert_blocked(inputs, R.TECHNICAL_REJECTED)
            size.assert_not_called()
        self.assertTrue(result.frame.frame_valid)
        self.assertFalse(result.frame.technical_eligible)
        self.assertTrue(result.frame.stop.valid)
        self.assertTrue(result.loss.calculation_valid)
        self.assertFalse(result.portfolio.blocks_new_entries)
        self.assertIn(R.TECHNICAL_REJECTED, result.skipped_stages[0].reasons)

    def test_missing_invalid_pe_and_unusable_stop_never_fall_back_to_close(self):
        for pe in (None, D("NaN"), 110.0, D(1)):
            with self.subTest(pe=pe):
                result = self.assert_blocked(replace(fixture(), pe=pe),
                                             R.STOP_UNAVAILABLE_OR_INVALID)
                self.assertTrue(result.frame.frame_valid)
                self.assertTrue(result.loss.calculation_valid)
                self.assertIsNone(result.sizing)
                self.assertTrue(result.frame.stop is None or not result.frame.stop.valid)

    def test_explicit_cost_equity_and_funds_required_without_defaulting(self):
        cases = (
            ("worst_case_extra_loss_per_unit", None, R.MISSING_COST_EVIDENCE),
            ("equity", None, R.MISSING_SIZING_INPUT),
            ("free_quote", None, R.MISSING_SIZING_INPUT),
            ("worst_case_extra_loss_per_unit", D(-1), R.INVALID_SIZING_INPUT),
            ("equity", D(0), R.INVALID_SIZING_INPUT),
            ("free_quote", D(-1), R.INVALID_SIZING_INPUT),
            ("free_quote", D(0), R.SIZING_REJECTED),
        )
        for name, value, reason in cases:
            with self.subTest(field=name, value=value):
                result = self.assert_blocked(replace(fixture(), **{name: value}), reason)
                self.assertTrue(result.loss.calculation_valid)
                if reason is R.SIZING_REJECTED:
                    self.assertIn(sizing.SizingReason.ZERO_BUYING_CAPACITY, result.sizing.reasons)
                else:
                    self.assertIn(name, result.invalid_fields)

    def test_individually_valid_but_different_cutoffs_cannot_be_combined(self):
        for name in ("loss_state", "portfolio_state"):
            with self.subTest(state=name):
                inputs = fixture()
                inputs = replace(inputs, **{name: replace(getattr(inputs, name), as_of=AS_OF + 1)})
                result = self.assert_blocked(inputs, R.CUTOFF_MISMATCH)
                self.assertTrue(result.loss.calculation_valid)
                self.assertTrue(result.portfolio.snapshot_valid)
                self.assertFalse(result.coherence_valid)

    def test_quote_account_and_equity_correlations_are_required(self):
        inputs = fixture()
        cases = (
            (replace(inputs, quote_asset="OTHER"), R.QUOTE_ASSET_MISMATCH),
            (replace(inputs, account_id="another-account"), R.ACCOUNT_ID_MISMATCH),
            (replace(inputs, equity=D(9999)), R.EQUITY_MISMATCH),
            (replace(inputs, account_id=""), R.INVALID_IDENTITY),
            (replace(inputs, loss_state=replace(inputs.loss_state, quote_asset="OTHER")),
             R.QUOTE_ASSET_MISMATCH),
            (replace(inputs, portfolio_state=replace(inputs.portfolio_state, quote_asset="OTHER")),
             R.QUOTE_ASSET_MISMATCH),
        )
        for case, reason in cases:
            with self.subTest(reason=reason, inputs=case):
                result = self.assert_blocked(case, reason)
                self.assertTrue(result.frame.frame_valid)
                self.assertTrue(result.loss.calculation_valid)
                self.assertTrue(result.portfolio.snapshot_valid)

    def test_missing_mismatched_and_invalid_loss_policies_fail_closed(self):
        inputs = fixture()
        cases = (
            (replace(inputs, profile=None), R.INVALID_PROFILE),
            (replace(inputs, profile="L0"), R.INVALID_PROFILE),
            (replace(inputs, profile=sizing.RiskProfile.R0), R.PROFILE_MISMATCH),
            (replace(inputs, loss_state=replace(inputs.loss_state, policy=replace(
                inputs.loss_state.policy, daily_loss_fraction=D(".5")))), R.LOSS_GUARD_REJECTED),
        )
        for case, reason in cases:
            with self.subTest(reason=reason):
                result = self.assert_blocked(case, reason)
                if reason is R.LOSS_GUARD_REJECTED:
                    self.assertIn(loss.LossGuardReason.INVALID_POLICY, result.loss.reasons)

    def test_daily_loss_drawdown_and_latched_breaches_prevent_sizing(self):
        for current, start, previous, expected in (
            (D(9950), D(10000), False, loss.LossGuardReason.DAILY_LOSS_LIMIT_REACHED),
            (D(9900), D(9900), False, loss.LossGuardReason.DRAWDOWN_LIMIT_REACHED),
            (D(10000), D(10000), True, loss.LossGuardReason.PREVIOUS_DAILY_BREACH),
        ):
            with self.subTest(reason=expected):
                inputs = fixture()
                inputs = replace(inputs, equity=current, loss_state=replace(
                    inputs.loss_state, current_equity=current, day_start_equity=start,
                    prior_daily_breach=previous,
                ))
                result = self.assert_blocked(inputs, R.LOSS_GUARD_BLOCKED)
                self.assertTrue(result.coherence_valid)
                self.assertIn(expected, result.loss.reasons)

    def test_positions_pending_unknown_orders_reservations_and_fills_block(self):
        for count in ("open_position_count", "outstanding_order_count", "uncertain_order_count",
                      "held_reservation_count", "unsettled_fill_count"):
            with self.subTest(count=count):
                inputs = fixture()
                state = replace(inputs.portfolio_state, **{count: 1})
                result = self.assert_blocked(replace(inputs, portfolio_state=state),
                                             R.PORTFOLIO_GUARD_BLOCKED)
                self.assertTrue(result.portfolio.snapshot_valid)
                self.assertTrue(result.portfolio.blocks_new_entries)
                self.assertEqual(result.portfolio, portfolio.assess_portfolio_entry(state))
                self.assertTrue(result.loss.calculation_valid)

    def test_invalid_missing_stale_evidence_and_containers_fail_closed(self):
        inputs = fixture()
        broken_frame = deepcopy(inputs.frame)
        broken_frame.candles_1h[-1].completed = False
        cases = (
            (replace(inputs, portfolio_state=None), R.PORTFOLIO_GUARD_REJECTED),
            (replace(inputs, portfolio_state=replace(inputs.portfolio_state,
                                                    positions_complete=False)), R.PORTFOLIO_GUARD_REJECTED),
            (replace(inputs, portfolio_state=replace(inputs.portfolio_state,
                         snapshot_observed_at=AS_OF - 31)), R.PORTFOLIO_GUARD_REJECTED),
            (replace(inputs, loss_state=None), R.LOSS_GUARD_REJECTED),
            (replace(inputs, frame=broken_frame), R.FRAME_REJECTED),
        )
        for case, reason in cases:
            with self.subTest(reason=reason):
                result = self.assert_blocked(case, reason)
                if reason is not R.LOSS_GUARD_REJECTED:
                    self.assertTrue(result.loss.calculation_valid)
                if reason is not R.FRAME_REJECTED:
                    self.assertTrue(result.frame.frame_valid)
        for invalid in (None, {}, "invalid"):
            with self.subTest(container=invalid):
                result = self.assert_blocked(invalid, R.INVALID_INPUT)
                self.assertEqual(tuple(s.stage for s in result.skipped_stages), tuple(S))

    def test_component_failure_preserves_other_results_and_reports_attempts(self):
        for function, stage in (("assess_loss_guards", S.LOSS_GUARD),
                                ("assess_market_frame", S.FRAME),
                                ("assess_portfolio_entry", S.PORTFOLIO_GUARD),
                                ("assess_position_size", S.SIZING)):
            with self.subTest(stage=stage):
                with patch.object(pipe, function, side_effect=RuntimeError("private details")):
                    result = pipe.assess_risk_pipeline(fixture())
                self.assertFalse(result.local_conditions_satisfied)
                self.assertFalse(result.executable_ready)
                self.assertIn(R.COMPONENT_FAILURE, result.reasons)
                self.assertEqual(result.failed_stages, (stage,))
                self.assertIsNone(result.sizing)
                self.assertNotIn("private details", repr(result))
                if stage is not S.FRAME:
                    self.assertTrue(result.frame.frame_valid)
                if stage is not S.LOSS_GUARD:
                    self.assertTrue(result.loss.calculation_valid)
                if stage is S.SIZING:
                    self.assertEqual(result.skipped_stages, ())
                else:
                    self.assertEqual(result.skipped_stages[0].stage, S.SIZING)
        with patch.object(pipe, "assess_market_frame", return_value=object()):
            result = pipe.assess_risk_pipeline(fixture())
            self.assertEqual(result.failed_stages, (S.FRAME,))
            self.assertIn(R.COMPONENT_FAILURE, result.reasons)

    def test_determinism_immutability_and_no_gate_or_atomic_admission(self):
        inputs = fixture()
        before = deepcopy(inputs)
        with patch.object(gate, "evaluate_entry", side_effect=AssertionError("No Gate")) as gate_call:
            first = pipe.assess_risk_pipeline(inputs)
            with localcontext() as context:
                context.prec = 3
                context.rounding = ROUND_UP
                context.traps[Inexact] = True
                second = pipe.assess_risk_pipeline(inputs)
            gate_call.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(inputs, before)
        # Repeated successes consume no capacity and establish no atomic admission.
        self.assertTrue(first.local_conditions_satisfied and second.local_conditions_satisfied)
        with self.assertRaises(FrozenInstanceError):
            inputs.pe = D(1)
        with self.assertRaises(FrozenInstanceError):
            first.executable_ready = True
        with self.assertRaises((TypeError, ValueError)):
            replace(first, executable_ready=True)
        inputs.frame.candles_15m[-1].close = D(100)
        self.assertEqual(first.frame.metadata.m15.candles[-1].close, D(101))
        self.assertFalse(hasattr(first, "admission_token"))
        self.assertFalse(hasattr(first, "order"))
        self.assertFalse(first.executable_ready)


if __name__ == "__main__":
    unittest.main()
