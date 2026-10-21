"""Risk safety checks with synthetic quantities, prices, and limits."""

import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext

from .gate import EntryRequest, RiskDecision, RiskLimits, RiskReason, evaluate_entry


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.request = EntryRequest(Decimal("2"), Decimal("10"))
        self.limits = RiskLimits(Decimal("5"), Decimal("100"))

    def test_missing_configuration_denies_entry(self):
        self.assertEqual(evaluate_entry(self.request),
                         RiskDecision(False, RiskReason.MISSING_CONFIG))
        self.assertFalse(evaluate_entry().allowed)

    def test_invalid_limit_values_deny_entry(self):
        invalid = (Decimal("0"), Decimal("-1"), Decimal("NaN"),
                   Decimal("sNaN"), Decimal("Infinity"),
                   Decimal("-Infinity"), 1, 0.5, True, "100", None)
        for value in invalid:
            for limits in (RiskLimits(value, Decimal("100")),
                           RiskLimits(Decimal("5"), value)):
                with self.subTest(value=repr(value), limits=limits):
                    self.assertEqual(evaluate_entry(self.request, limits),
                                     RiskDecision(False, RiskReason.INVALID_CONFIG))

    def test_invalid_quantities_and_prices_deny_entry(self):
        invalid = (Decimal("0"), Decimal("-1"), Decimal("NaN"),
                   Decimal("sNaN"), Decimal("Infinity"),
                   Decimal("-Infinity"), 2, 1.5, False, "10", None)
        for value in invalid:
            for request in (EntryRequest(value, Decimal("10")),
                            EntryRequest(Decimal("2"), value)):
                with self.subTest(value=repr(value), request=request):
                    self.assertEqual(evaluate_entry(request, self.limits),
                                     RiskDecision(False, RiskReason.INVALID_INPUT))

    def test_wrong_structures_deny_entry(self):
        for value in (None, {}, "request", object()):
            with self.subTest(value=value):
                self.assertEqual(evaluate_entry(value, self.limits),
                                 RiskDecision(False, RiskReason.INVALID_INPUT))
        self.assertEqual(evaluate_entry(self.request, {}),
                         RiskDecision(False, RiskReason.INVALID_CONFIG))

    def test_quantity_limit_is_enforced(self):
        request = EntryRequest(Decimal("6"), Decimal("1"))
        self.assertEqual(evaluate_entry(request, self.limits),
                         RiskDecision(False, RiskReason.LIMIT_EXCEEDED))

    def test_notional_limit_is_enforced(self):
        request = EntryRequest(Decimal("5"), Decimal("21"))
        self.assertEqual(evaluate_entry(request, self.limits),
                         RiskDecision(False, RiskReason.LIMIT_EXCEEDED))

    def test_valid_request_returns_only_a_risk_decision(self):
        result = evaluate_entry(self.request, self.limits)
        self.assertEqual(result, RiskDecision(True, RiskReason.WITHIN_LIMITS))
        self.assertFalse(hasattr(result, "execute"))
        self.assertFalse(hasattr(result, "submit"))
        with self.assertRaises(FrozenInstanceError):
            result.allowed = False

    def test_exact_limit_boundaries_are_permitted(self):
        request = EntryRequest(Decimal("5"), Decimal("20"))
        self.assertTrue(evaluate_entry(request, self.limits).allowed)

    def test_small_decimal_quantity_is_not_coerced_or_rounded(self):
        request = EntryRequest(Decimal("0.1"), Decimal("0.2"))
        limits = RiskLimits(Decimal("1"), Decimal("0.02"))
        self.assertTrue(evaluate_entry(request, limits).allowed)

    def test_rounded_notional_cannot_hide_a_limit_breach(self):
        request = EntryRequest(Decimal("1.00000000000000000000000000001"),
                               Decimal("1"))
        limits = RiskLimits(Decimal("2"), Decimal("1"))
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(evaluate_entry(request, limits),
                             RiskDecision(False, RiskReason.LIMIT_EXCEEDED))
            self.assertEqual(context.prec, 2)

    def test_unrepresentable_arithmetic_denies_entry(self):
        request = EntryRequest(Decimal("1e999999"), Decimal("1e999999"))
        limits = RiskLimits(Decimal("1e999999"), Decimal("1e999999"))
        self.assertEqual(evaluate_entry(request, limits),
                         RiskDecision(False, RiskReason.INVALID_INPUT))

    def test_underflow_cannot_bypass_notional_limit(self):
        request = EntryRequest(Decimal("1e-999999"), Decimal("1e-999999"))
        limits = RiskLimits(Decimal("1"), Decimal("1e-999999"))
        self.assertEqual(evaluate_entry(request, limits),
                         RiskDecision(False, RiskReason.INVALID_INPUT))


if __name__ == "__main__":
    unittest.main()
