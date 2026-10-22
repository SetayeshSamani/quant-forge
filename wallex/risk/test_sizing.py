"""Hand-calculated synthetic sizing and independent exact budget checks."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import (
    Context, Decimal, Inexact, Overflow, ROUND_CEILING, ROUND_DOWN,
    ROUND_FLOOR, ROUND_UP, Subnormal, Underflow, localcontext,
)
from fractions import Fraction
import subprocess
import sys
import unittest
from unittest.mock import patch

from . import sizing
from .gate import EntryRequest, RiskDecision, RiskLimits, RiskReason, evaluate_entry

D = Decimal
R = sizing.SizingReason


def fixture(**changes):
    """All numbers are synthetic; even the zero cost claim is explicit."""
    inputs = sizing.SizingInputs(
        equity=D(10000), free_quote=D(10000), quote_asset="SYNTHETIC-QUOTE",
        pe=D(100), initial_stop=D(95), worst_case_extra_loss_per_unit=D(0),
        risk_fraction=D("0.001"), max_position_fraction=D("0.20"),
    )
    return replace(inputs, **changes)


class SizingTests(unittest.TestCase):
    def assess(self, **changes):
        result = sizing.assess_position_size(fixture(**changes))
        self.assertTrue(result.calculation_valid, result)
        self.assertFalse(result.executable_ready)
        return result

    def assert_failure(self, inputs, reason):
        result = sizing.assess_position_size(inputs)
        self.assertFalse(result.calculation_valid)
        self.assertIn(reason, result.reasons)
        for field in ("inputs", "unit_loss", "risk_budget", "position_budget",
                      "notional_budget", "theoretical_quantity_upper_bound"):
            self.assertIsNone(getattr(result, field))
        self.assertFalse(result.executable_ready)
        self.assertIsNotNone(result.rejected_field)
        return result

    def test_risk_budget_limited_quantity(self):
        # Loss/base=5, risk=10000*0.001=10, quantity=2; notional cap=20.
        result = self.assess()
        self.assertEqual(result.unit_loss, D(5))
        self.assertEqual(result.risk_budget, D(10))
        self.assertEqual(result.notional_budget, D(2000))
        self.assertEqual(result.theoretical_quantity_upper_bound, D(2))

    def test_position_notional_budget_limited_quantity(self):
        # Risk cap=1000/5=200; position cap=2000/100=20.
        result = self.assess(risk_fraction=D("0.1"))
        self.assertEqual(result.position_budget, D(2000))
        self.assertEqual(result.theoretical_quantity_upper_bound, D(20))

    def test_free_funds_limited_quantity(self):
        # Position budget=2000 but free quote=1500; gross quantity cap=15.
        result = self.assess(risk_fraction=D("0.1"), free_quote=D(1500))
        self.assertEqual(result.notional_budget, D(1500))
        self.assertEqual(result.theoretical_quantity_upper_bound, D(15))

    def test_explicit_l0_profile_numbers(self):
        policy = sizing.policy_for(sizing.RiskProfile.L0)
        self.assertEqual((policy.risk_fraction, policy.max_position_fraction),
                         (D("0.001"), D("0.20")))
        result = self.assess(risk_fraction=policy.risk_fraction,
                             max_position_fraction=policy.max_position_fraction)
        self.assertEqual((result.risk_budget, result.position_budget,
                          result.theoretical_quantity_upper_bound), (D(10), D(2000), D(2)))

    def test_explicit_r0_profile_numbers(self):
        policy = sizing.policy_for(sizing.RiskProfile.R0)
        self.assertEqual((policy.risk_fraction, policy.max_position_fraction),
                         (D("0.0025"), D("0.30")))
        result = self.assess(risk_fraction=policy.risk_fraction,
                             max_position_fraction=policy.max_position_fraction)
        self.assertEqual((result.risk_budget, result.position_budget,
                          result.theoretical_quantity_upper_bound), (D(25), D(3000), D(5)))

    def test_profile_lookup_has_no_default_or_activation(self):
        with self.assertRaises(TypeError):
            sizing.policy_for()
        for value in (None, "L0", "R0", 0, True):
            with self.assertRaises(ValueError):
                sizing.policy_for(value)
        sizing.policy_for(sizing.RiskProfile.L0)
        self.assert_failure(fixture(risk_fraction=None), R.MISSING_POLICY)
        self.assert_failure(fixture(max_position_fraction=None), R.MISSING_POLICY)

    def test_exact_both_budget_boundaries(self):
        result = self.assess(initial_stop=D(90), max_position_fraction=D("0.01"))
        self.assertEqual(result.theoretical_quantity_upper_bound, D(1))
        self.assertEqual(result.risk_budget, D(10))
        self.assertEqual(result.notional_budget, D(100))
        q = Fraction(result.theoretical_quantity_upper_bound)
        self.assertEqual(q*10, 10)
        self.assertEqual(q*100, 100)

    def test_extra_cost_increases_loss_and_reduces_quantity(self):
        baseline = self.assess()
        result = self.assess(worst_case_extra_loss_per_unit=D(5))
        self.assertEqual(result.unit_loss, D(10))
        self.assertEqual(result.theoretical_quantity_upper_bound, D(1))
        self.assertLess(result.theoretical_quantity_upper_bound, baseline.theoretical_quantity_upper_bound)
        self.assertIn(R.PROVISIONAL_COST_EVIDENCE, result.reasons)

    def test_missing_cost_evidence_never_defaults_to_zero(self):
        self.assertIsNone(sizing.SizingInputs().worst_case_extra_loss_per_unit)
        self.assert_failure(fixture(worst_case_extra_loss_per_unit=None), R.MISSING_COST_EVIDENCE)
        self.assertTrue(self.assess(worst_case_extra_loss_per_unit=D(0)).calculation_valid)

    def test_recurring_risk_division_rounds_down(self):
        result = self.assess(equity=D(1000), pe=D(10), initial_stop=D(7),
                             max_position_fraction=D(1))
        q = result.theoretical_quantity_upper_bound
        self.assertEqual(q, D("0."+"3"*34))
        self.assertLess(Fraction(q)*3, 1)
        self.assertGreater((Fraction(q)+Fraction(D("1e-34")))*3, 1)

    def test_recurring_notional_division_rounds_down(self):
        result = self.assess(equity=D(10), free_quote=D(1), pe=D(3),
                             initial_stop=D(1), risk_fraction=D(1), max_position_fraction=D(1))
        q = result.theoretical_quantity_upper_bound
        self.assertEqual(q, D("0."+"3"*34))
        self.assertLess(Fraction(q)*3, 1)
        self.assertGreater((Fraction(q)+Fraction(D("1e-34")))*3, 1)

    def test_very_small_decimal_quantity_remains_positive(self):
        result = self.assess(equity=D("1e-97"), free_quote=D("1e-97"),
                             pe=D("1e100"), initial_stop=D("9e99"))
        self.assertEqual(result.risk_budget, D("1e-100"))
        self.assertEqual(result.unit_loss, D("1e99"))
        self.assertEqual(result.theoretical_quantity_upper_bound, D("1e-199"))

    def test_high_precision_price_gap_is_not_lost(self):
        pe = D("1.0000000000000000000000000000000001")
        result = self.assess(equity=D(1), free_quote=D(1), pe=pe, initial_stop=D(1),
                             worst_case_extra_loss_per_unit=D("1e-34"),
                             risk_fraction=D("1e-34"), max_position_fraction=D(1))
        self.assertEqual(result.inputs.pe.as_tuple(), pe.as_tuple())
        self.assertEqual(result.unit_loss, D("2e-34"))
        self.assertEqual(result.risk_budget, D("1e-34"))
        self.assertEqual(result.theoretical_quantity_upper_bound, D("0.5"))

    def test_far_apart_price_scales_are_calculated_exactly(self):
        result = self.assess(pe=D("1e200"), initial_stop=D("1e-200"),
                             worst_case_extra_loss_per_unit=D("1e-200"))
        self.assertEqual(result.unit_loss, D("1e200"))
        self.assertEqual(result.theoretical_quantity_upper_bound, D("1e-199"))

    def test_exact_input_digit_and_exponent_limits_are_supported(self):
        result = self.assess(equity=D("9"*100+"e200"), free_quote=D("1e200"),
                             pe=D("1e100"), initial_stop=D("9e99"))
        self.assertEqual(result.notional_budget, D("1e200"))
        self.assertEqual(result.theoretical_quantity_upper_bound, D("1e100"))

    def test_caller_decimal_context_and_flags_do_not_leak(self):
        inputs = fixture(pe=D(10), initial_stop=D(7), equity=D(1000))
        expected = sizing.assess_position_size(inputs)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.Emax, context.Emin = 1, -1
            context.traps[Inexact] = True
            before = context.copy()
            actual = sizing.assess_position_size(inputs)
            self.assertEqual(actual, expected)
            self.assertEqual((context.prec, context.rounding, context.Emax, context.Emin),
                             (before.prec, before.rounding, before.Emax, before.Emin))
            self.assertEqual(context.flags, before.flags)
            self.assertEqual(context.traps, before.traps)

    def test_quote_identifier_and_units_are_preserved(self):
        result = self.assess(quote_asset="QuoteCase")
        self.assertEqual(result.inputs.quote_asset, "QuoteCase")

    def test_missing_or_wrong_input_container(self):
        for inputs in (None, {}, (), "inputs", object()):
            self.assert_failure(inputs, R.INVALID_INPUT)
        self.assert_failure(sizing.SizingInputs(), R.INVALID_EQUITY)

    def test_invalid_monetary_values_without_coercion(self):
        for field, reason in (("equity", R.INVALID_EQUITY), ("free_quote", R.INVALID_FREE_QUOTE),
                              ("pe", R.INVALID_PE), ("initial_stop", R.INVALID_STOP),
                              ("worst_case_extra_loss_per_unit", R.INVALID_COST)):
            bad = [None, True, 100, "100", 100.0, D(-1), D("NaN"), D("sNaN"),
                   D("Infinity"), D("-Infinity")]
            if field not in ("free_quote", "worst_case_extra_loss_per_unit"):
                bad.append(D(0))
            for value in bad:
                with self.subTest(field=field, value=repr(value)):
                    expected = R.MISSING_COST_EVIDENCE if field == "worst_case_extra_loss_per_unit" and value is None else reason
                    self.assert_failure(fixture(**{field: value}), expected)

    def test_invalid_quote_identity(self):
        for asset in (None, "", " ", "A B", "A\x00", "A\x7f", 1, "A"*201):
            self.assert_failure(fixture(quote_asset=asset), R.INVALID_QUOTE_ASSET)

    def test_stop_equal_to_or_above_pe(self):
        for stop in (D(100), D(101)):
            self.assert_failure(fixture(initial_stop=stop), R.STOP_NOT_BELOW_PE)

    def test_invalid_fractions_and_percentage_unit_values(self):
        for field, reason in (("risk_fraction", R.INVALID_RISK_FRACTION),
                              ("max_position_fraction", R.INVALID_POSITION_FRACTION)):
            for value in (None, True, "0.1", 0.1, 1, D(0), D(-1), D("1.01"), D(20),
                          D("NaN"), D("sNaN"), D("Infinity")):
                with self.subTest(field=field, value=repr(value)):
                    self.assert_failure(fixture(**{field: value}), R.MISSING_POLICY if value is None else reason)
        self.assertTrue(self.assess(risk_fraction=D(1), max_position_fraction=D(1)).calculation_valid)

    def test_zero_available_buying_capacity(self):
        self.assert_failure(fixture(free_quote=D(0)), R.ZERO_BUYING_CAPACITY)

    def test_extreme_exponents_and_excess_digits_reject_before_arithmetic(self):
        for field in ("equity", "free_quote", "pe", "initial_stop", "worst_case_extra_loss_per_unit",
                      "risk_fraction", "max_position_fraction"):
            for value in (D("1e999999"), D("1e-999999"), D("1."+"1"*100)):
                with self.subTest(field=field, value=value):
                    self.assert_failure(fixture(**{field: value}), R.NUMERIC_RANGE_EXCEEDED)
        self.assert_failure(fixture(worst_case_extra_loss_per_unit=D("0e-201")), R.NUMERIC_RANGE_EXCEEDED)

    def test_decimal_overflow_is_fail_closed(self):
        # Fault injection exercises the guard if arithmetic settings regress.
        context = Context(prec=512, Emax=2, Emin=-999, traps=[Overflow])
        with patch.object(sizing, "_exact_context", return_value=context):
            self.assert_failure(fixture(), R.ARITHMETIC_FAILURE)

    def test_decimal_underflow_is_fail_closed(self):
        context = Context(prec=34, Emax=999, Emin=-2, rounding=ROUND_FLOOR,
                          traps=[Underflow, Subnormal])
        with patch.object(sizing, "_quantity_context", return_value=context):
            self.assert_failure(fixture(equity=D("1e-97"), free_quote=D("1e-97"),
                                        pe=D("1e100"), initial_stop=D("9e99")), R.ARITHMETIC_FAILURE)

    def test_precision_loss_is_detected_by_exact_post_validation(self):
        context = Context(prec=1, Emax=999, Emin=-999, rounding=ROUND_DOWN, traps=[])
        with patch.object(sizing, "_exact_context", return_value=context):
            self.assert_failure(fixture(equity=D(1000), pe=D(10), initial_stop=D(7),
                                        worst_case_extra_loss_per_unit=D("0.1")), R.PRECISION_FAILURE)

    def test_upward_quantity_rounding_cannot_pass_exact_budget_check(self):
        context = Context(prec=34, Emax=999, Emin=-999, rounding=ROUND_CEILING, traps=[])
        with patch.object(sizing, "_quantity_context", return_value=context):
            self.assert_failure(fixture(equity=D(1000), pe=D(10), initial_stop=D(7)), R.BUDGET_VIOLATION)

    def test_nonpositive_computed_quantity_is_never_published(self):
        context = Context(prec=1, Emax=999, Emin=0, rounding=ROUND_FLOOR, traps=[])
        with patch.object(sizing, "_quantity_context", return_value=context):
            self.assert_failure(fixture(equity=D("1e-97"), free_quote=D("1e-97"),
                                        pe=D("1e100"), initial_stop=D("9e99")), R.NON_POSITIVE_QUANTITY)

    def test_independent_exact_budget_checks_across_synthetic_cases(self):
        for free in (D("0.01"), D(1), D(10), D(10000)):
            for cost in (D(0), D("0.1"), D(1), D(20)):
                with self.subTest(free=free, cost=cost):
                    inputs = fixture(free_quote=free, worst_case_extra_loss_per_unit=cost)
                    result = sizing.assess_position_size(inputs)
                    self.assertTrue(result.calculation_valid)
                    q = Fraction(result.theoretical_quantity_upper_bound)
                    self.assertGreater(q, 0)
                    self.assertLessEqual(q*(5+Fraction(cost)), 10)
                    self.assertLessEqual(q*100, min(Fraction(free), 2000))

    def test_inputs_policy_and_assessment_are_immutable(self):
        inputs = fixture()
        before = deepcopy(inputs)
        result = sizing.assess_position_size(inputs)
        self.assertEqual(inputs, before)
        policy = sizing.policy_for(sizing.RiskProfile.L0)
        for target, name, value in ((inputs, "pe", D(1)), (policy, "risk_fraction", D(1)),
                                    (result, "calculation_valid", False),
                                    (result, "executable_ready", True)):
            with self.assertRaises(FrozenInstanceError):
                setattr(target, name, value)
        with self.assertRaises((ValueError, TypeError)):
            replace(result, executable_ready=True)

    def test_pure_result_is_not_risk_approval_or_trade_intent(self):
        result = self.assess()
        self.assertNotIsInstance(result, (EntryRequest, RiskDecision))
        for name in ("approved_quantity", "trading_authorized", "executable_order", "submit", "execute"):
            self.assertFalse(hasattr(result, name))
        self.assertIn(R.NOT_TRADING_AUTHORIZATION, result.reasons)
        self.assertFalse(evaluate_entry(result, RiskLimits(D(100), D(10000))).allowed)

    def test_existing_gate_behavior_is_not_activated_by_sizing(self):
        result = self.assess()
        self.assertTrue(result.calculation_valid)
        self.assertEqual(evaluate_entry(), RiskDecision(False, RiskReason.MISSING_CONFIG))
        self.assertEqual(evaluate_entry(EntryRequest(D(2), D(100)), RiskLimits(D(2), D(200))),
                         RiskDecision(True, RiskReason.WITHIN_LIMITS))

    def test_import_does_not_require_teammate_modules_or_invoke_gate(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('wallex.market', 'wallex.execution', 'wallex.audit', 'contracts', 'shared')):
        raise AssertionError('Forbidden external module dependency')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from wallex.risk.sizing import assess_position_size
assert not assess_position_size().calculation_valid
"""
        p = subprocess.run([sys.executable, "-B", "-c", code],capture_output=True,text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)


if __name__ == "__main__":
    unittest.main()
