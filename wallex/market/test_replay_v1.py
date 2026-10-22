"""Replay selection tests use explicit, invented per-candle availability evidence."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import unittest

from wallex.market.frame_v1 import (
    FrameV1Error, QualityV1, V1ErrorCode, build_market_frame_v1,
)
from wallex.market.frame_v1_example import synthetic_inputs_v1
from wallex.market.normalization import ErrorCode, Timeframe
from wallex.market.replay_v1 import AvailabilityReasonV1, select_available_history_v1


class ReplayV1Tests(unittest.TestCase):
    def setUp(self):
        self.inputs = synthetic_inputs_v1()
        self.history = self.inputs["candles_15m"]

    def select(self, history=None, *, as_of=903604, timeframe=Timeframe.M15):
        return select_available_history_v1(
            self.history if history is None else history, symbol=self.inputs["symbol"],
            timeframe=timeframe, as_of=as_of,
        )

    def assert_failure(self, code, history=None, **options):
        with self.assertRaises(FrameV1Error) as caught:
            self.select(history, **options)
        self.assertIs(caught.exception.code, code)
        return caught.exception

    def test_closed_bar_is_not_available_until_its_actual_receipt(self):
        waiting = self.select(as_of=903600)
        self.assertEqual(len(waiting.candles), 20)
        self.assertFalse(waiting.latest_expected_available)
        self.assertFalse(waiting.minimum_history_met)
        self.assertFalse(waiting.complete)
        self.assertEqual(len(waiting.excluded), 1)
        self.assertEqual(waiting.excluded[0].index, 20)
        self.assertIs(waiting.excluded[0].reason, AvailabilityReasonV1.NOT_RECEIVED)
        self.assertEqual(waiting.excluded[0].timestamp, 902700)
        received = self.select(as_of=903601)
        self.assertTrue(received.complete)
        self.assertEqual(received.candles, self.history)
        self.assertEqual(received.excluded, ())

    def test_h1_availability_is_independent_of_m15_decision_boundaries(self):
        h1 = self.inputs["candles_1h"]
        before_close = self.select(h1, timeframe=Timeframe.H1, as_of=903599)
        self.assertEqual(len(before_close.candles), 249)
        self.assertTrue(before_close.latest_expected_available)
        self.assertFalse(before_close.minimum_history_met)
        self.assertIs(before_close.excluded[0].reason, AvailabilityReasonV1.FUTURE_CANDLE)
        before_receipt = self.select(h1, timeframe=Timeframe.H1, as_of=903600)
        self.assertFalse(before_receipt.latest_expected_available)
        self.assertIs(before_receipt.excluded[0].reason, AvailabilityReasonV1.NOT_RECEIVED)
        self.assertTrue(self.select(h1, timeframe=Timeframe.H1, as_of=903601).complete)

    def test_frame_builder_rejects_incomplete_selection_and_accepts_available_history(self):
        waiting = self.select(as_of=903600)
        with self.assertRaises(FrameV1Error) as caught:
            build_market_frame_v1(**(self.inputs | dict(
                candles_15m=waiting.candles, quote=None, depth=None, as_of=903600,
            )))
        self.assertIs(caught.exception.code, V1ErrorCode.INSUFFICIENT_HISTORY)
        frame = build_market_frame_v1(**(self.inputs | dict(
            candles_15m=self.select(as_of=903601).candles,
            candles_1h=self.select(self.inputs["candles_1h"], timeframe=Timeframe.H1,
                                  as_of=903601).candles,
            quote=None, depth=None, as_of=903601,
        )))
        self.assertIs(frame.quality, QualityV1.INCOMPLETE)
        self.assertEqual(frame.received_time, 903601)
        self.assertFalse(frame.executable_ready)

    def test_internal_unavailable_bar_is_not_filled_or_hidden(self):
        history = list(self.history)
        for receipt in (None, 903605):
            with self.subTest(receipt=receipt):
                history[5] = replace(self.history[5], received_time=receipt)
                error = self.assert_failure(ErrorCode.MISSING_CANDLE, history)
                self.assertIsNotNone(error.__cause__)

    def test_unknown_last_receipt_is_explicitly_excluded_without_inventing_time(self):
        history = self.history[:-1] + (replace(self.history[-1], received_time=None),)
        result = self.select(history)
        self.assertFalse(result.complete)
        self.assertEqual(result.candles, self.history[:-1])
        self.assertIs(result.excluded[0].reason, AvailabilityReasonV1.MISSING_RECEIVED_TIME)
        self.assertIsNone(history[-1].received_time)

    def test_open_and_future_records_are_excluded_with_distinct_reasons(self):
        last = self.history[-1]
        future = replace(last, candle=replace(last.candle, timestamp=903600), received_time=904501)
        opened = replace(last, candle=replace(last.candle, timestamp=904500, completed=False),
                         received_time=903604)
        result = self.select(self.history + (future, opened))
        self.assertEqual(result.candles, self.history)
        self.assertTrue(result.complete)
        self.assertEqual(tuple(item.reason for item in result.excluded),
                         (AvailabilityReasonV1.FUTURE_CANDLE, AvailabilityReasonV1.OPEN_CANDLE))
        self.assertEqual(tuple(item.index for item in result.excluded), (21, 22))
        # Exclusion metadata cannot expose future OHLCV values to a decision.
        self.assertFalse(hasattr(result.excluded[0], "candle"))

    def test_missing_latest_bar_is_incomplete_even_with_enough_older_history(self):
        first = self.history[0]
        older = replace(first, candle=replace(first.candle, timestamp=first.candle.timestamp - 900),
                        received_time=first.received_time - 900)
        result = self.select((older,) + self.history[:-1])
        self.assertTrue(result.minimum_history_met)
        self.assertFalse(result.latest_expected_available)
        self.assertFalse(result.complete)

    def test_empty_history_is_explicitly_incomplete(self):
        result = self.select(())
        self.assertEqual(result.candles, ())
        self.assertEqual(result.excluded, ())
        self.assertFalse(result.complete)

    def test_duplicate_unsorted_and_gapped_inputs_are_not_repaired(self):
        for history, code in (
            ((self.history[0],) + self.history, ErrorCode.DUPLICATE_TIMESTAMP),
            ((self.history[0], self.history[1], self.history[0]), ErrorCode.DUPLICATE_TIMESTAMP),
            ((self.history[1], self.history[0]), ErrorCode.OUT_OF_ORDER),
            (self.history[:5] + self.history[6:], ErrorCode.MISSING_CANDLE),
        ):
            with self.subTest(code=code):
                self.assert_failure(code, history)

    def test_invalid_ohlcv_and_alignment_still_use_normalizer(self):
        first = self.history[0]
        for changes, code in ((dict(high=Decimal(98)), ErrorCode.INVALID_OHLC),
                              (dict(volume=Decimal(-1)), ErrorCode.NEGATIVE_VOLUME),
                              (dict(timestamp=first.candle.timestamp + 1), ErrorCode.UNALIGNED_TIMESTAMP)):
            with self.subTest(changes=changes):
                history = (replace(first, candle=replace(first.candle, **changes)),) + self.history[1:]
                error = self.assert_failure(code, history)
                self.assertIsNotNone(error.__cause__)

    def test_strict_identity_times_completion_and_config_even_before_exclusion(self):
        first = self.history[0]
        for value in (True, 903604.0, "903604", -1):
            with self.subTest(value=value):
                self.assert_failure(V1ErrorCode.INVALID_TIMESTAMP, as_of=value)
                self.assert_failure(V1ErrorCode.INVALID_TIMESTAMP,
                                    (replace(first, received_time=value),))
                bad = replace(first, candle=replace(first.candle, timestamp=value, completed=False))
                self.assert_failure(V1ErrorCode.INVALID_TIMESTAMP, (bad,))
        self.assert_failure(V1ErrorCode.INVALID_TIME_ORDER,
                            (replace(first, received_time=first.candle.timestamp),))
        self.assert_failure(V1ErrorCode.HISTORY_IDENTITY_MISMATCH,
                            (replace(first, candle=replace(first.candle, symbol="OTHER")),))
        self.assert_failure(ErrorCode.INVALID_COMPLETION,
                            (replace(first, candle=replace(first.candle, completed=1)),))
        self.assert_failure(V1ErrorCode.INVALID_CONFIG, timeframe="M15")
        self.assert_failure(V1ErrorCode.INVALID_HISTORY, "not observations")

    def test_selection_is_repeatable_immutable_and_preserves_inputs(self):
        before = deepcopy(self.history)
        result = self.select()
        self.assertEqual(self.history, before)
        self.assertEqual(result, self.select())
        with self.assertRaises(FrozenInstanceError):
            result.latest_expected_available = False
        with self.assertRaises(FrozenInstanceError):
            result.candles[0].received_time = 0


if __name__ == "__main__":
    unittest.main()
