"""Lifecycle and execution refusal tests; no real orders or APIs are used."""

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from .runtime import LiveTradingDisabledError, Runtime, RuntimeState


class RuntimeTests(unittest.TestCase):
    def test_defaults_to_stopped_with_live_trading_disabled(self):
        runtime = Runtime()
        self.assertEqual(runtime.state, RuntimeState.STOPPED)
        self.assertIs(runtime.live_trading_enabled, False)

    def test_normal_lifecycle_returns_new_models(self):
        original = Runtime()
        current = original
        for state in (RuntimeState.STARTING, RuntimeState.RUNNING,
                      RuntimeState.STOPPING, RuntimeState.STOPPED):
            current = current.transition(state)
            self.assertEqual(current.state, state)
            self.assertIs(current.live_trading_enabled, False)
        self.assertEqual(original.state, RuntimeState.STOPPED)
        self.assertIsNot(current, original)

    def test_degraded_state_can_recover_or_stop(self):
        starting = Runtime().transition(RuntimeState.STARTING)
        degraded = starting.transition(RuntimeState.DEGRADED)
        running = degraded.transition(RuntimeState.RUNNING)
        degraded_again = running.transition(RuntimeState.DEGRADED)
        self.assertEqual(degraded_again.transition(RuntimeState.STOPPING).state,
                         RuntimeState.STOPPING)
        self.assertEqual(starting.transition(RuntimeState.STOPPING).state,
                         RuntimeState.STOPPING)

    def test_all_state_pairs_follow_the_transition_policy(self):
        valid = {
            (RuntimeState.STOPPED, RuntimeState.STARTING),
            (RuntimeState.STARTING, RuntimeState.RUNNING),
            (RuntimeState.STARTING, RuntimeState.DEGRADED),
            (RuntimeState.STARTING, RuntimeState.STOPPING),
            (RuntimeState.RUNNING, RuntimeState.DEGRADED),
            (RuntimeState.RUNNING, RuntimeState.STOPPING),
            (RuntimeState.DEGRADED, RuntimeState.RUNNING),
            (RuntimeState.DEGRADED, RuntimeState.STOPPING),
            (RuntimeState.STOPPING, RuntimeState.STOPPED),
        }
        for source in RuntimeState:
            for target in RuntimeState:
                with self.subTest(source=source, target=target):
                    runtime = Runtime(source)
                    if (source, target) in valid:
                        result = runtime.transition(target)
                        self.assertEqual(result.state, target)
                        self.assertIs(result.live_trading_enabled, False)
                    else:
                        with self.assertRaises(ValueError):
                            runtime.transition(target)
                        self.assertEqual(runtime.state, source)

    def test_malformed_states_fail_closed(self):
        for value in (None, "RUNNING", 1, True, {}, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Runtime(value)
                runtime = Runtime()
                with self.assertRaises(ValueError):
                    runtime.transition(value)
                self.assertEqual(runtime.state, RuntimeState.STOPPED)
                self.assertIs(runtime.live_trading_enabled, False)

    def test_live_trading_cannot_be_enabled(self):
        runtime = Runtime()
        with self.assertRaises(TypeError):
            Runtime(live_trading_enabled=True)
        with self.assertRaises(FrozenInstanceError):
            runtime.live_trading_enabled = True
        with self.assertRaises(FrozenInstanceError):
            runtime.state = RuntimeState.RUNNING
        self.assertIs(runtime.live_trading_enabled, False)

    def test_orders_are_refused_in_every_state_without_calling_payload(self):
        payload = Mock()
        for state in RuntimeState:
            with self.subTest(state=state):
                runtime = Runtime(state)
                with self.assertRaises(LiveTradingDisabledError):
                    runtime.execute_order(payload)
                self.assertEqual(runtime.state, state)
                self.assertIs(runtime.live_trading_enabled, False)
        self.assertEqual(payload.mock_calls, [])

    def test_missing_or_malformed_order_is_also_refused(self):
        runtime = Runtime()
        for payload in (None, {}, "order", 1):
            with self.subTest(payload=payload):
                with self.assertRaises(LiveTradingDisabledError):
                    runtime.execute_order(payload)
        with self.assertRaises(LiveTradingDisabledError):
            runtime.execute_order()


if __name__ == "__main__":
    unittest.main()
