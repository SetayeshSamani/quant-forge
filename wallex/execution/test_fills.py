from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from wallex.audit.ledger import Ledger, ObservationError, StorageError
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_fill, synthetic_observation, synthetic_request
from wallex.execution.gateway import ExecutionRefused, ReplayGateway
from wallex.execution.models import OrderState
from wallex.execution.replay import ReplayScenario, ReplayTransport


class FillReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.request = synthetic_request()
        self.key = (self.request.intent.account_id, self.request.intent.client_id)

    def gateway(self, observation, *, lookups=(), cancellation=None, ledger=None):
        source = ReplayTransport({self.key: ReplayScenario(observation, cancellation=cancellation)},
                                 lookups={self.key: lookups})
        return ReplayGateway(ledger or self.ledger, source, clock=lambda: FIXTURE_TIME), source

    def test_repeated_source_fill_and_redelivery_time_do_not_duplicate_quantity_or_fee(self):
        fill = synthetic_fill()
        original = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill, fill))
        redelivered = replace(fill, received_at=FIXTURE_TIME + timedelta(seconds=1))
        repeated = replace(original, fills=(redelivered,), observed_at=FIXTURE_TIME + timedelta(seconds=1))
        gateway, source = self.gateway(original, lookups=(repeated, repeated))
        first = gateway.submit(self.request).receipt
        second = gateway.reconcile(self.request.intent.intent_id)
        third = gateway.reconcile(self.request.intent.intent_id)
        self.assertEqual(first.cumulative_filled_quantity, Decimal("1"))
        self.assertEqual(second.cumulative_filled_quantity, third.cumulative_filled_quantity)
        self.assertEqual(second.known_net_acquired_base, Decimal("0.99"))
        self.assertEqual(len(second.fills), 1)
        self.assertEqual(second.fills[0].received_at, FIXTURE_TIME)
        self.assertEqual(len([e for e in self.ledger.events() if e.kind == "fill_recorded"]), 1)
        self.assertEqual(len(source.submit_calls), 1)

    def test_partial_cancel_race_retains_late_fill_and_same_economic_outcome(self):
        first = synthetic_fill(fill_id="fill-1", quantity=Decimal("1"), fee=Decimal("0.01"))
        late = synthetic_fill(fill_id="fill-2", quantity=Decimal("0.5"), fee=Decimal("0.02"),
                              source_time=FIXTURE_TIME + timedelta(seconds=1),
                              received_at=FIXTURE_TIME + timedelta(seconds=4))
        outcomes = []
        for arrives_after_cancel in (True, False):
            ledger = Ledger(Path(self.directory.name) / f"race-{arrives_after_cancel}.sqlite3")
            before = (first,) if arrives_after_cancel else (first, late)
            cumulative = Decimal("1") if arrives_after_cancel else Decimal("1.5")
            initial = synthetic_observation(state=OrderState.PARTIAL, cumulative=cumulative, fills=before,
                                             observed_at=FIXTURE_TIME + timedelta(seconds=2))
            canceled = replace(initial, state=OrderState.CANCELED,
                               observed_at=FIXTURE_TIME + timedelta(seconds=3))
            final = synthetic_observation(state=OrderState.CANCELED, cumulative=Decimal("1.5"),
                                           fills=(first, late), observed_at=FIXTURE_TIME + timedelta(seconds=5))
            gateway, _ = self.gateway(initial, lookups=(final,), cancellation=canceled, ledger=ledger)
            gateway.submit(self.request)
            gateway.cancel(self.request.intent.intent_id)
            result = gateway.reconcile(self.request.intent.intent_id)
            self.assertEqual(result.state, OrderState.CANCELED)
            self.assertEqual(result.cumulative_filled_quantity, Decimal("1.5"))
            self.assertEqual(result.remaining_quantity_base, Decimal("0.5"))
            self.assertTrue(result.reservation_held)
            self.assertIsNone(result.sellable_quantity_base)
            outcomes.append((result.known_net_acquired_base, ledger.reserved(self.key[0], "QUOTE")))
        self.assertEqual(outcomes, [(Decimal("1.47"), Decimal("21"))] * 2)

    def test_filled_buy_keeps_base_asset_fees_and_does_not_claim_account_flat_or_sellable(self):
        fill = synthetic_fill(quantity=Decimal("2"), fee=Decimal("0.02"))
        observation = synthetic_observation(state=OrderState.FILLED, cumulative=Decimal("2"), fills=(fill,))
        gateway, _ = self.gateway(observation)
        receipt = gateway.submit(self.request).receipt
        self.assertEqual(receipt.known_net_acquired_base, Decimal("1.98"))
        self.assertEqual(receipt.remaining_quantity_base, Decimal(0))
        self.assertIsNone(receipt.sellable_quantity_base)
        self.assertEqual(receipt.sellable_unknown_reason, "account_free_balance_not_reconciled")
        self.assertTrue(receipt.reservation_held)
        with self.assertRaises(ExecutionRefused):
            gateway.submit(synthetic_request(intent_id="second-entry", client_id="second-client"))

    def test_unknown_fee_and_source_time_remain_unknown_with_reasons(self):
        fill = synthetic_fill(fee=None, fee_asset=None, source_time=None)
        observation = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill,))
        gateway, _ = self.gateway(observation)
        receipt = gateway.submit(self.request).receipt
        self.assertEqual(receipt.state, OrderState.PARTIAL)
        self.assertIsNone(receipt.known_net_acquired_base)
        self.assertEqual(receipt.net_quantity_unknown_reason, "one_or_more_fill_fees_unknown")
        self.assertIsNone(receipt.to_dict()["fills"][0]["fee_amount"])
        self.assertIsNone(receipt.fills[0].source_time)
        self.assertTrue(receipt.fills[0].source_time_unknown_reason)

    def test_conflicting_repeated_fill_never_overwrites_the_first_record(self):
        fill = synthetic_fill()
        initial = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill,))
        conflicting = replace(initial, fills=(replace(fill, price_quote_per_base=Decimal("9")),),
                              observed_at=FIXTURE_TIME + timedelta(seconds=1))
        gateway, _ = self.gateway(initial, lookups=(conflicting,))
        gateway.submit(self.request)
        receipt = gateway.reconcile(self.request.intent.intent_id)
        self.assertEqual(receipt.state, OrderState.UNKNOWN)
        self.assertEqual(receipt.fills, (fill,))
        self.assertIsNone(receipt.cumulative_filled_quantity)
        self.assertTrue(receipt.reservation_held)
        unusable = [e for e in self.ledger.events() if e.kind == "source_observation_unusable"]
        self.assertEqual(unusable[0].payload["observation"]["fills"][0]["price_quote_per_base"], "9")

    def test_missing_fill_details_cannot_fabricate_a_known_cumulative_position(self):
        observation = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=())
        gateway, _ = self.gateway(observation)
        receipt = gateway.submit(self.request).receipt
        self.assertEqual(receipt.state, OrderState.UNKNOWN)
        self.assertEqual(receipt.fills, ())
        self.assertIsNone(receipt.known_net_acquired_base)
        unusable = [e for e in self.ledger.events() if e.kind == "source_observation_unusable"]
        self.assertEqual(unusable[0].payload["observation"]["cumulative_filled_quantity"], "1")

    def test_stale_or_decreasing_observations_do_not_erase_fill_evidence(self):
        fill = synthetic_fill()
        first = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill,))
        lower = synthetic_observation(observed_at=FIXTURE_TIME + timedelta(seconds=1))
        gateway, _ = self.gateway(first, lookups=(lower,))
        gateway.submit(self.request)
        receipt = gateway.reconcile(self.request.intent.intent_id)
        self.assertEqual(receipt.state, OrderState.UNKNOWN)
        self.assertEqual(receipt.fills, (fill,))

    def test_source_fill_identity_cannot_be_reassigned_to_a_different_intent(self):
        fill = synthetic_fill()
        first = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill,))
        gateway, _ = self.gateway(first)
        gateway.submit(self.request)
        second = synthetic_request(intent_id="second", client_id="second")
        record, _ = self.ledger.plan(second, at=FIXTURE_TIME)
        record = self.ledger.transition(second.intent.intent_id, OrderState.SUBMITTING,
                                        expected_version=record.version, at=FIXTURE_TIME, reason="test_only")
        rebound = synthetic_fill(second, fill_id=fill.fill_id, source_order_id="other-order")
        observation = synthetic_observation(second, state=OrderState.PARTIAL, cumulative=Decimal("1"),
                                             fills=(rebound,), source_order_id="other-order")
        with self.assertRaises(ObservationError):
            self.ledger.observe(second.intent.intent_id, observation, expected_version=record.version,
                                at=FIXTURE_TIME)
        self.assertEqual(self.ledger.fills(self.request.intent.intent_id), (fill,))
        self.assertEqual(self.ledger.fills(second.intent.intent_id), ())

    def test_fill_audit_failure_rolls_back_fills_and_receipt_then_recovers_without_resubmission(self):
        fill = synthetic_fill()
        observation = synthetic_observation(state=OrderState.PARTIAL, cumulative=Decimal("1"), fills=(fill,))
        gateway, source = self.gateway(observation)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_fill BEFORE INSERT ON audit_events "
                       "WHEN NEW.kind='fill_recorded' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
            db.commit()
        with self.assertRaises(StorageError):
            gateway.submit(self.request)
        self.assertEqual(self.ledger.fills(self.request.intent.intent_id), ())
        self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.SUBMITTING)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER fail_fill")
            db.commit()
        restarted = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        receipt = restarted.recover_account(self.key[0], run_id="restart", trace_id="restart")[0]
        self.assertEqual(receipt.state, OrderState.PARTIAL)
        self.assertEqual(receipt.fills, (fill,))
        self.assertEqual(source.submit_calls, [self.key])

    def test_partial_quantity_math_is_exact_even_in_small_decimal_context(self):
        quantity = Decimal("0.123456789123456789123456789123")
        fill = synthetic_fill(quantity=quantity, fee=Decimal(0))
        observation = synthetic_observation(state=OrderState.PARTIAL, cumulative=quantity, fills=(fill,))
        gateway, _ = self.gateway(observation)
        with localcontext() as context:
            context.prec = 4
            receipt = gateway.submit(self.request).receipt
        self.assertEqual(receipt.remaining_quantity_base, Decimal("1.876543210876543210876543210877"))
        self.assertEqual(receipt.known_net_acquired_base, quantity)


if __name__ == "__main__":
    unittest.main()
