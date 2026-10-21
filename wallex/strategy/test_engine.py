"""Synthetic fixtures for a strategy that must never emit an entry signal."""

import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from .engine import DecisionReason, Signal, StrategyInput, evaluate


class StrategyTests(unittest.TestCase):
    def test_missing_data_is_no_trade(self):
        for value in (None, StrategyInput(), StrategyInput("TEST"),
                      StrategyInput(" ", (Decimal("10"),))):
            with self.subTest(value=value):
                result = evaluate(value)
                self.assertEqual(result.signal, Signal.NO_TRADE)
                self.assertEqual(result.reason, DecisionReason.MISSING_DATA)

    def test_invalid_prices_fail_closed(self):
        for price in (Decimal("0"), Decimal("-1"), Decimal("NaN"),
                      Decimal("sNaN"), Decimal("Infinity"),
                      Decimal("-Infinity"), 10, 1.5, True, "10", None):
            with self.subTest(price=repr(price)):
                result = evaluate(StrategyInput("TEST", (price,)))
                self.assertEqual(result.signal, Signal.NO_TRADE)
                self.assertEqual(result.reason, DecisionReason.INVALID_INPUT)

    def test_invalid_shapes_fail_closed(self):
        for value in ({}, "TEST", StrategyInput(None, (Decimal("10"),)),
                      StrategyInput("TEST", [Decimal("10")]),
                      StrategyInput("TEST", None)):
            with self.subTest(value=value):
                result = evaluate(value)
                self.assertEqual(result.signal, Signal.NO_TRADE)
                self.assertEqual(result.reason, DecisionReason.INVALID_INPUT)

    def test_valid_prices_are_not_a_confirmed_signal(self):
        inputs = StrategyInput("TEST", (Decimal("10"), Decimal("11")))
        result = evaluate(inputs)
        self.assertEqual(result.signal, Signal.NO_TRADE)
        self.assertEqual(result.reason, DecisionReason.CALCULATIONS_NOT_IMPLEMENTED)
        self.assertEqual(evaluate(inputs), result)
        self.assertEqual(inputs.close_prices, (Decimal("10"), Decimal("11")))

    def test_decisions_are_immutable(self):
        result = evaluate()
        with self.assertRaises(FrozenInstanceError):
            result.signal = "BUY"


if __name__ == "__main__":
    unittest.main()
