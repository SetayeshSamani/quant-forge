"""Synthetic, hand-calculated inclusive loss limits and persistent-latch boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import (
    Context, Decimal, Inexact, Overflow, ROUND_DOWN, ROUND_UP,
    Subnormal, Underflow, localcontext,
)
import subprocess
import sys
import unittest
from unittest.mock import patch

from . import gate, loss_guards as guards, sizing

D = Decimal
R = guards.LossGuardReason
DAY = 172800
AS_OF = DAY + 3600


def fixture(**changes):
    """Caller evidence is entirely synthetic; both False flags are explicit."""
    state = guards.LossGuardState(
        current_equity=D(10000), day_start_equity=D(10000), high_water_equity=D(10000),
        quote_asset="SYNTHETIC-QUOTE", as_of=AS_OF, day_start_at=DAY,
        equity_observed_at=AS_OF, policy=guards.loss_policy_for(sizing.RiskProfile.L0),
        prior_daily_breach=False, prior_drawdown_breach=False,
    )
    return replace(state, **changes)


class LossGuardTests(unittest.TestCase):
    def assess(self, **changes):
        result = guards.assess_loss_guards(fixture(**changes))
        self.assertTrue(result.calculation_valid, result)
        self.assertFalse(result.executable_ready)
        return result

    def assert_failure(self, state, reason):
        result = guards.assess_loss_guards(state)
        self.assertFalse(result.calculation_valid)
        self.assertTrue(result.blocks_new_entries)
        self.assertFalse(result.executable_ready)
        self.assertIn(reason, result.reasons)
        for name in ("state", "daily_loss", "daily_loss_budget", "current_drawdown",
                     "drawdown_budget", "daily_breach_latched", "drawdown_breach_latched"):
            self.assertIsNone(getattr(result, name))
        self.assertIsNotNone(result.rejected_field)
        return result

    def test_explicit_l0_threshold_numbers(self):
        policy = guards.loss_policy_for(sizing.RiskProfile.L0)
        self.assertIs(policy.profile, sizing.RiskProfile.L0)
        self.assertEqual((policy.daily_loss_fraction, policy.max_drawdown_fraction),
                         (D("0.005"), D("0.01")))
        result = self.assess(policy=policy)
        self.assertEqual((result.daily_loss_budget, result.drawdown_budget), (D(50), D(100)))
        self.assertEqual(result.reasons, (R.WITHIN_LOCAL_LIMITS,))
        self.assertFalse(result.blocks_new_entries)

    def test_explicit_r0_threshold_numbers(self):
        policy = guards.loss_policy_for(sizing.RiskProfile.R0)
        self.assertEqual((policy.daily_loss_fraction, policy.max_drawdown_fraction),
                         (D("0.01"), D("0.02")))
        result = self.assess(policy=policy)
        self.assertEqual((result.daily_loss_budget, result.drawdown_budget), (D(100), D(200)))
        self.assertFalse(result.blocks_new_entries)

    def test_no_default_or_implicitly_activated_profile(self):
        with self.assertRaises(TypeError):
            guards.loss_policy_for()
        for value in (None, "L0", "R0", 0, True):
            with self.assertRaises(ValueError):
                guards.loss_policy_for(value)
        guards.loss_policy_for(sizing.RiskProfile.R0)
        self.assert_failure(fixture(policy=None), R.MISSING_STATE)
        self.assertIsNone(guards.LossGuardState().policy)

    def test_daily_loss_limit_equality_blocks_entry(self):
        result = self.assess(current_equity=D(9950))
        self.assertEqual((result.daily_loss, result.daily_loss_budget), (D(50), D(50)))
        self.assertEqual(result.current_drawdown, D(50))
        self.assertEqual(result.reasons, (R.DAILY_LOSS_LIMIT_REACHED,))
        self.assertTrue(result.blocks_new_entries and result.daily_breach_latched)
        self.assertFalse(result.drawdown_breach_latched)

    def test_drawdown_limit_equality_blocks_entry(self):
        result = self.assess(current_equity=D(9900), day_start_equity=D(9900))
        self.assertEqual(result.daily_loss, D(0))
        self.assertEqual((result.current_drawdown, result.drawdown_budget), (D(100), D(100)))
        self.assertEqual(result.reasons, (R.DRAWDOWN_LIMIT_REACHED,))
        self.assertTrue(result.blocks_new_entries and result.drawdown_breach_latched)
        self.assertFalse(result.daily_breach_latched)

    def test_r0_equality_for_each_metric_independently(self):
        policy = guards.loss_policy_for(sizing.RiskProfile.R0)
        daily = self.assess(policy=policy, current_equity=D(9900))
        peak = self.assess(policy=policy, current_equity=D(9800), day_start_equity=D(9800))
        self.assertEqual(daily.reasons, (R.DAILY_LOSS_LIMIT_REACHED,))
        self.assertEqual((daily.daily_loss, daily.daily_loss_budget), (D(100), D(100)))
        self.assertEqual(peak.reasons, (R.DRAWDOWN_LIMIT_REACHED,))
        self.assertEqual((peak.current_drawdown, peak.drawdown_budget), (D(200), D(200)))

    def test_just_below_and_above_daily_limit(self):
        below = self.assess(current_equity=D("9950.01"))
        above = self.assess(current_equity=D("9949.99"))
        self.assertEqual(below.daily_loss, D("49.99"))
        self.assertFalse(below.blocks_new_entries)
        self.assertEqual(above.daily_loss, D("50.01"))
        self.assertEqual(above.reasons, (R.DAILY_LOSS_LIMIT_REACHED,))

    def test_just_below_and_above_drawdown_limit(self):
        below = self.assess(current_equity=D("9900.01"), day_start_equity=D("9900.01"))
        above = self.assess(current_equity=D("9899.99"), day_start_equity=D("9899.99"))
        self.assertEqual(below.current_drawdown, D("99.99"))
        self.assertFalse(below.blocks_new_entries)
        self.assertEqual(above.current_drawdown, D("100.01"))
        self.assertEqual(above.reasons, (R.DRAWDOWN_LIMIT_REACHED,))

    def test_gain_during_day_never_becomes_negative_loss(self):
        result = self.assess(current_equity=D(10100), high_water_equity=D(10100))
        self.assertEqual((result.daily_loss, result.current_drawdown), (D(0), D(0)))
        self.assertEqual((result.daily_loss_budget, result.drawdown_budget), (D(50), D(101)))
        self.assertFalse(result.blocks_new_entries)

    def test_gain_can_still_have_peak_to_current_drawdown(self):
        result = self.assess(current_equity=D(10800), high_water_equity=D(11000))
        self.assertEqual(result.daily_loss, D(0))
        self.assertEqual((result.current_drawdown, result.drawdown_budget), (D(200), D(110)))
        self.assertEqual(result.reasons, (R.DRAWDOWN_LIMIT_REACHED,))

    def test_previous_daily_breach_blocks_after_full_recovery(self):
        result = self.assess(prior_daily_breach=True)
        self.assertEqual((result.daily_loss, result.current_drawdown), (D(0), D(0)))
        self.assertEqual(result.reasons, (R.PREVIOUS_DAILY_BREACH,))
        self.assertTrue(result.blocks_new_entries and result.daily_breach_latched)

    def test_previous_drawdown_breach_blocks_after_full_recovery(self):
        result = self.assess(prior_drawdown_breach=True)
        self.assertEqual(result.reasons, (R.PREVIOUS_DRAWDOWN_BREACH,))
        self.assertTrue(result.blocks_new_entries and result.drawdown_breach_latched)

    def test_multiple_reasons_have_stable_order(self):
        result = self.assess(current_equity=D(9800), prior_daily_breach=True,
                             prior_drawdown_breach=True)
        self.assertEqual(result.reasons, (R.DAILY_LOSS_LIMIT_REACHED, R.DRAWDOWN_LIMIT_REACHED,
                                         R.PREVIOUS_DAILY_BREACH, R.PREVIOUS_DRAWDOWN_BREACH))
        self.assertTrue(result.daily_breach_latched and result.drawdown_breach_latched)

    def test_caller_carried_latches_survive_recreated_state(self):
        first = self.assess(current_equity=D(9800))
        recovered = fixture(prior_daily_breach=first.daily_breach_latched,
                            prior_drawdown_breach=first.drawdown_breach_latched,
                            as_of=AS_OF+100, equity_observed_at=AS_OF+100)
        result = guards.assess_loss_guards(recovered)
        self.assertEqual(result.reasons, (R.PREVIOUS_DAILY_BREACH, R.PREVIOUS_DRAWDOWN_BREACH))
        self.assertTrue(result.blocks_new_entries)

    def test_new_utc_day_does_not_automatically_clear_prior_flags(self):
        result = self.assess(as_of=AS_OF+86400, day_start_at=DAY+86400,
                             equity_observed_at=AS_OF+86400, prior_daily_breach=True)
        self.assertEqual(result.reasons, (R.PREVIOUS_DAILY_BREACH,))
        self.assertTrue(result.daily_breach_latched)

    def test_repeated_calls_are_deterministic_and_do_not_mutate_inputs(self):
        state = fixture(current_equity=D(9950))
        before = deepcopy(state)
        first = guards.assess_loss_guards(state)
        self.assertEqual(first, guards.assess_loss_guards(state))
        self.assertEqual(state, before)
        self.assertFalse(state.prior_daily_breach)
        self.assertTrue(first.daily_breach_latched)

    def test_missing_required_state_evidence_fails_closed(self):
        self.assert_failure(None, R.MISSING_STATE)
        self.assert_failure(guards.LossGuardState(), R.MISSING_STATE)
        for field in guards.LossGuardState.__dataclass_fields__:
            with self.subTest(field=field):
                self.assert_failure(fixture(**{field: None}), R.MISSING_STATE)

    def test_wrong_state_containers(self):
        for value in ({}, [], "state", object(), sizing.SizingInputs()):
            self.assert_failure(value, R.INVALID_STATE)

    def test_invalid_equity_without_numeric_coercion(self):
        for field in ("current_equity", "day_start_equity", "high_water_equity"):
            for value in (D(0), D(-1), D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"),
                          10000, 10000.0, True, "10000"):
                with self.subTest(field=field, value=repr(value)):
                    self.assert_failure(fixture(**{field: value}), R.INVALID_EQUITY_BASELINE)

    def test_high_water_must_cover_both_current_and_day_start(self):
        for changes in ({"high_water_equity": D(9999)},
                        {"current_equity": D(10100)},
                        {"current_equity": D(9900), "high_water_equity": D(9950)}):
            self.assert_failure(fixture(**changes), R.INVALID_EQUITY_BASELINE)

    def test_breach_flags_are_required_actual_bools(self):
        for field in ("prior_daily_breach", "prior_drawdown_breach"):
            for value in (None, 0, 1, "False", D(0), [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_failure(fixture(**{field: value}),
                                        R.MISSING_STATE if value is None else R.INVALID_STATE)

    def test_invalid_or_unagreed_policy_thresholds(self):
        policy = guards.loss_policy_for(sizing.RiskProfile.L0)
        for invalid in ({}, "L0", sizing.policy_for(sizing.RiskProfile.L0),
                        replace(policy, profile="L0"), replace(policy, profile=None)):
            self.assert_failure(fixture(policy=invalid), R.INVALID_POLICY)
        for field, wrong_fraction in (("daily_loss_fraction", D("0.01")),
                                      ("max_drawdown_fraction", D("0.02"))):
            for value in (None, 0.005, True, "0.005", D(0), D(-1), D("NaN"),
                          D("sNaN"), D("Infinity"), wrong_fraction):
                self.assert_failure(fixture(policy=replace(policy, **{field: value})), R.INVALID_POLICY)

    def test_utc_day_boundary_and_epoch(self):
        for cutoff, start in ((DAY-1, DAY-86400), (DAY, DAY), (DAY+86399, DAY),
                              (DAY+86400, DAY+86400), (0, 0)):
            with self.subTest(cutoff=cutoff):
                self.assertTrue(self.assess(as_of=cutoff, day_start_at=start,
                                            equity_observed_at=cutoff).calculation_valid)

    def test_incorrect_day_start_and_local_timezone_offset_rejected(self):
        for start in (DAY-86400, DAY+1, DAY+12600, DAY+86400):
            self.assert_failure(fixture(day_start_at=start), R.INCONSISTENT_TIMESTAMPS)

    def test_future_or_previous_day_observation_rejected(self):
        for observed in (AS_OF+1, DAY-1):
            self.assert_failure(fixture(equity_observed_at=observed), R.INCONSISTENT_TIMESTAMPS)
        self.assertTrue(self.assess(equity_observed_at=DAY).calculation_valid)
        self.assertTrue(self.assess(equity_observed_at=AS_OF).calculation_valid)

    def test_wrong_timestamp_types_and_negative_values(self):
        for field in ("as_of", "day_start_at", "equity_observed_at"):
            for value in (True, 0.0, "172800", D(DAY), -1):
                with self.subTest(field=field, value=value):
                    self.assert_failure(fixture(**{field: value}), R.INCONSISTENT_TIMESTAMPS)

    def test_quote_identity_validation_and_exact_preservation(self):
        for value in ("", " ", "A B", "A\x00", "A\x7f", 1, "A"*201):
            self.assert_failure(fixture(quote_asset=value), R.INVALID_STATE)
        self.assertEqual(self.assess(quote_asset="QuoteCase").state.quote_asset, "QuoteCase")

    def test_high_decimal_precision_just_below_limit(self):
        current = D("9950.0000000000000000000000000001")
        result = self.assess(current_equity=current)
        self.assertEqual(result.daily_loss, D("49."+"9"*28))
        self.assertFalse(result.blocks_new_entries)
        self.assertEqual(result.state.current_equity.as_tuple(), current.as_tuple())

    def test_tiny_equities_preserve_exact_inclusive_limit(self):
        result = self.assess(current_equity=D("9.95e-191"), day_start_equity=D("1e-190"),
                             high_water_equity=D("1e-190"))
        self.assertEqual((result.daily_loss, result.daily_loss_budget), (D("5e-193"), D("5e-193")))
        self.assertEqual(result.reasons, (R.DAILY_LOSS_LIMIT_REACHED,))

    def test_caller_decimal_context_independence_and_flags(self):
        state = fixture(current_equity=D("9950.0000000000000000000000000001"))
        expected = guards.assess_loss_guards(state)
        with localcontext() as context:
            context.prec, context.rounding = 1, ROUND_UP
            context.Emax, context.Emin = 1, -1
            context.traps[Inexact] = True
            before = context.copy()
            self.assertEqual(guards.assess_loss_guards(state), expected)
            self.assertEqual(context.flags, before.flags)
            self.assertEqual(context.traps, before.traps)
            self.assertEqual((context.prec, context.rounding, context.Emax, context.Emin),
                             (before.prec, before.rounding, before.Emax, before.Emin))

    def test_numeric_resource_limits_fail_closed(self):
        for field in ("current_equity", "day_start_equity", "high_water_equity"):
            for value in (D("1e999999"), D("1e-999999"), D("1."+"1"*100)):
                self.assert_failure(fixture(**{field: value}), R.NUMERIC_RANGE_EXCEEDED)

    def test_arithmetic_overflow_or_underflow_never_returns_approval(self):
        overflow = Context(prec=512, Emax=1, Emin=-999, traps=[Overflow])
        with patch.object(guards, "_exact_context", return_value=overflow):
            self.assert_failure(fixture(), R.ARITHMETIC_FAILURE)
        underflow = Context(prec=512, Emax=999, Emin=-2, traps=[Underflow, Subnormal])
        with patch.object(guards, "_exact_context", return_value=underflow):
            self.assert_failure(fixture(current_equity=D("9.95e-101"),
                                        day_start_equity=D("1e-100"),
                                        high_water_equity=D("1e-100")), R.ARITHMETIC_FAILURE)

    def test_precision_loss_cannot_hide_a_limit(self):
        context = Context(prec=1, Emax=999, Emin=-999, rounding=ROUND_DOWN, traps=[])
        with patch.object(guards, "_exact_context", return_value=context):
            self.assert_failure(fixture(current_equity=D(9951)), R.PRECISION_FAILURE)

    def test_state_policy_and_output_are_immutable(self):
        state = fixture()
        result = guards.assess_loss_guards(state)
        for target, name, value in ((state, "prior_daily_breach", True),
                                    (state.policy, "daily_loss_fraction", D(1)),
                                    (result, "blocks_new_entries", True),
                                    (result, "executable_ready", True)):
            with self.assertRaises(FrozenInstanceError):
                setattr(target, name, value)
        with self.assertRaises((ValueError, TypeError)):
            replace(result, executable_ready=True)

    def test_local_success_is_not_executable_or_complete_risk_permission(self):
        result = self.assess()
        self.assertFalse(result.blocks_new_entries or result.executable_ready)
        self.assertNotIsInstance(result, (gate.RiskDecision, gate.EntryRequest))
        for name in ("approved_quantity", "theoretical_quantity_upper_bound", "trade_intent",
                     "trading_authorized", "submit", "execute"):
            self.assertFalse(hasattr(result, name))

    def test_gate_and_sizing_are_not_called_or_changed_by_guard(self):
        with patch.object(gate, "evaluate_entry", side_effect=AssertionError("gate invoked")) as g, \
                patch.object(sizing, "assess_position_size", side_effect=AssertionError("sizing invoked")) as s:
            self.assertFalse(self.assess().blocks_new_entries)
        g.assert_not_called()
        s.assert_not_called()
        self.assertFalse(gate.evaluate_entry().allowed)
        request = sizing.SizingInputs(equity=D(10000), free_quote=D(10000), quote_asset="Q",
            pe=D(100), initial_stop=D(95), worst_case_extra_loss_per_unit=D(0),
            risk_fraction=D("0.001"), max_position_fraction=D("0.20"))
        self.assertEqual(sizing.assess_position_size(request).theoretical_quantity_upper_bound, D(2))

    def test_no_external_module_or_runtime_dependency(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('wallex.market', 'wallex.execution', 'wallex.audit', 'wallex.strategy', 'wallex.infra', 'contracts', 'shared')):
        raise AssertionError('Forbidden module dependency')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from wallex.risk.loss_guards import assess_loss_guards
assert assess_loss_guards().blocks_new_entries
"""
        result = subprocess.run([sys.executable, "-B", "-c", code],capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
