"""Lean synthetic snapshot checks; no account collection or order admission."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import patch

from . import gate, loss_guards, portfolio_guards as guards, sizing

R = guards.PortfolioGuardReason


def fixture(**changes):
    """Explicit synthetic completeness and zero counts, never production defaults."""
    state = guards.PortfolioGuardState(
        account_id="synthetic-account", quote_asset="SYNTHETIC-QUOTE",
        as_of=1000, snapshot_observed_at=990, max_snapshot_age_seconds=30,
        open_position_count=0, outstanding_order_count=0, uncertain_order_count=0,
        held_reservation_count=0, unsettled_fill_count=0,
        positions_complete=True, orders_complete=True, reservations_complete=True,
        settlement_complete=True, account_scope_complete=True,
    )
    return replace(state, **changes)


class PortfolioGuardTests(unittest.TestCase):
    def assert_rejected(self, state, reason):
        result = guards.assess_portfolio_entry(state)
        self.assertFalse(result.snapshot_valid)
        self.assertTrue(result.blocks_new_entries)
        self.assertFalse(result.executable_ready)
        self.assertIn(reason, result.reasons)
        self.assertIsNone(result.state)
        self.assertIsNone(result.snapshot_age_seconds)
        self.assertIsNotNone(result.rejected_field)
        return result

    def test_complete_flat_account_passes_local_checks(self):
        state = fixture()
        result = guards.assess_portfolio_entry(state)
        self.assertTrue(result.snapshot_valid)
        self.assertFalse(result.blocks_new_entries)
        self.assertEqual(result.reasons, (R.NO_LOCAL_PORTFOLIO_BLOCKER,))
        self.assertEqual(result.snapshot_age_seconds, 10)
        self.assertEqual(result.state, state)
        self.assertFalse(result.executable_ready)

    def test_one_or_multiple_positions_block_account_wide(self):
        for count in (1, 2):
            with self.subTest(count=count):
                result = guards.assess_portfolio_entry(fixture(open_position_count=count))
                self.assertTrue(result.snapshot_valid and result.blocks_new_entries)
                self.assertIn(R.OPEN_POSITION_EXISTS, result.reasons)
                self.assertEqual(R.MULTIPLE_POSITIONS_DETECTED in result.reasons, count > 1)

    def test_pending_buy_and_sell_both_block_new_buy(self):
        # The producer aggregates BOTH sides; this entry guard has no side filter.
        for side in ("BUY", "SELL"):
            with self.subTest(pending_side=side):
                result = guards.assess_portfolio_entry(fixture(outstanding_order_count=1))
                self.assertTrue(result.blocks_new_entries)
                self.assertEqual(result.reasons, (R.OUTSTANDING_ORDER_EXISTS,))

    def test_unknown_orders_and_held_reservations_never_imply_flatness(self):
        cases = (
            ({"uncertain_order_count": 1}, (R.UNCERTAIN_ORDER_EXISTS,)),
            ({"held_reservation_count": 1}, (R.HELD_RESERVATION_EXISTS,)),
            ({"outstanding_order_count": 1, "uncertain_order_count": 1,
              "held_reservation_count": 1}, (R.OUTSTANDING_ORDER_EXISTS,
                                             R.UNCERTAIN_ORDER_EXISTS, R.HELD_RESERVATION_EXISTS)),
        )
        for changes, reasons in cases:
            with self.subTest(changes=changes):
                result = guards.assess_portfolio_entry(fixture(**changes))
                self.assertTrue(result.blocks_new_entries)
                self.assertEqual(result.reasons, reasons)

    def test_unsettled_fill_uncertainty_blocks(self):
        result = guards.assess_portfolio_entry(fixture(unsettled_fill_count=1))
        self.assertTrue(result.blocks_new_entries)
        self.assertEqual(result.reasons, (R.UNSETTLED_FILL_EXISTS,))

    def test_missing_or_incomplete_evidence_never_defaults_to_flat(self):
        self.assert_rejected(None, R.INCOMPLETE_ACCOUNT_EVIDENCE)
        self.assert_rejected(guards.PortfolioGuardState(), R.INCOMPLETE_ACCOUNT_EVIDENCE)
        for name in guards.PortfolioGuardState.__dataclass_fields__:
            with self.subTest(missing=name):
                self.assert_rejected(fixture(**{name: None}), R.INCOMPLETE_ACCOUNT_EVIDENCE)
        for name in ("positions_complete", "orders_complete", "reservations_complete",
                     "settlement_complete", "account_scope_complete"):
            for value, reason in ((False, R.INCOMPLETE_ACCOUNT_EVIDENCE), (1, R.INVALID_ACCOUNT_STATE)):
                with self.subTest(flag=name, value=value):
                    self.assert_rejected(fixture(**{name: value}), reason)

    def test_invalid_counts_identity_and_container_fail_closed(self):
        changes = (
            {"open_position_count": -1}, {"outstanding_order_count": True},
            {"uncertain_order_count": "0"}, {"held_reservation_count": 0.0},
            {"unsettled_fill_count": -1}, {"account_id": ""}, {"account_id": " account"},
            {"account_id": 1}, {"quote_asset": "A B"}, {"quote_asset": "Q\x00"},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_rejected(fixture(**change), R.INVALID_ACCOUNT_STATE)
        self.assert_rejected({}, R.INVALID_ACCOUNT_STATE)

    def test_future_stale_timestamps_and_explicit_age_boundaries(self):
        rejected = (
            ({"snapshot_observed_at": 1001}, R.FUTURE_ACCOUNT_SNAPSHOT),
            ({"snapshot_observed_at": 969}, R.STALE_ACCOUNT_SNAPSHOT),
            ({"max_snapshot_age_seconds": 0, "snapshot_observed_at": 999}, R.STALE_ACCOUNT_SNAPSHOT),
            ({"as_of": True}, R.INVALID_ACCOUNT_STATE),
            ({"snapshot_observed_at": 990.0}, R.INVALID_ACCOUNT_STATE),
            ({"snapshot_observed_at": -1}, R.INVALID_ACCOUNT_STATE),
            ({"max_snapshot_age_seconds": -1}, R.INVALID_SNAPSHOT_AGE_POLICY),
            ({"max_snapshot_age_seconds": True}, R.INVALID_SNAPSHOT_AGE_POLICY),
            ({"max_snapshot_age_seconds": 30.0}, R.INVALID_SNAPSHOT_AGE_POLICY),
        )
        for change, reason in rejected:
            with self.subTest(change=change):
                self.assert_rejected(fixture(**change), reason)
        for change, age in (({"snapshot_observed_at": 970}, 30),
                            ({"max_snapshot_age_seconds": 0, "snapshot_observed_at": 1000}, 0),
                            ({"as_of": 0, "snapshot_observed_at": 0, "max_snapshot_age_seconds": 0}, 0)):
            with self.subTest(boundary=change):
                result = guards.assess_portfolio_entry(fixture(**change))
                self.assertFalse(result.blocks_new_entries)
                self.assertEqual(result.snapshot_age_seconds, age)

    def test_immutable_inputs_and_deterministic_results(self):
        state = fixture()
        before = deepcopy(state)
        result = guards.assess_portfolio_entry(state)
        self.assertEqual(result, guards.assess_portfolio_entry(state))
        self.assertEqual(state, before)
        for target, name, value in ((state, "open_position_count", 1),
                                    (result, "blocks_new_entries", True),
                                    (result, "executable_ready", True)):
            with self.assertRaises(FrozenInstanceError):
                setattr(target, name, value)
        with self.assertRaises((ValueError, TypeError)):
            replace(result, executable_ready=True)

    def test_local_success_never_authorizes_or_calls_other_risk_checks(self):
        with patch.object(gate, "evaluate_entry") as g, \
                patch.object(sizing, "assess_position_size") as s, \
                patch.object(loss_guards, "assess_loss_guards") as loss:
            result = guards.assess_portfolio_entry(fixture())
        g.assert_not_called()
        s.assert_not_called()
        loss.assert_not_called()
        self.assertFalse(result.blocks_new_entries or result.executable_ready)
        self.assertNotIsInstance(result, gate.RiskDecision)
        for name in ("approved_quantity", "trade_intent", "submit", "execute", "admission_token"):
            self.assertFalse(hasattr(result, name))

    def test_two_snapshot_assessments_do_not_reserve_atomic_admission(self):
        snapshot = fixture()
        first = guards.assess_portfolio_entry(snapshot)
        second = guards.assess_portfolio_entry(snapshot)
        # Both pass: neither consumes capacity nor records a planned entry.
        self.assertFalse(first.blocks_new_entries or second.blocks_new_entries)
        self.assertEqual(snapshot.open_position_count, 0)
        self.assertEqual(snapshot.held_reservation_count, 0)
        self.assertFalse(first.executable_ready or second.executable_ready)


if __name__ == "__main__":
    unittest.main()
