from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from wallex.audit.ledger import IdentityConflict, Ledger, StorageError
from wallex.audit.writer import WriterBusy
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_observation, synthetic_request
from wallex.execution.gateway import ExecutionRefused, LiveUnavailable, RecoveryRequired, ReplayGateway
from wallex.execution.models import Mode, OrderState
from wallex.execution.replay import ReplayScenario, ReplayTransport
from wallex.strategy import StrategyInput, evaluate


class ReplayGatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.request = synthetic_request()
        self.key = (self.request.intent.account_id, self.request.intent.client_id)

    def gateway(self, *, loss=False, lookups=None, cancellation=None):
        source = ReplayTransport({self.key: ReplayScenario(synthetic_observation(), loss,
                                                          cancellation=cancellation)}, lookups=lookups)
        return ReplayGateway(self.ledger, source, clock=lambda: FIXTURE_TIME), source

    def trigger(self, kind):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events "
                       "WHEN NEW.kind=? BEGIN SELECT RAISE(ABORT, 'fixture'); END".replace("?", repr(kind)))
            db.commit()

    def test_committed_intent_reservation_and_submitting_audit_precede_io(self):
        gateway, source = self.gateway()
        actual_submit = source.submit
        def observed(intent):
            # A second SQLite connection can take a write transaction: no DB
            # transaction from the gateway is open across this I/O boundary.
            observer = Ledger(self.path)
            self.assertEqual(observer.get(intent.intent_id).state, OrderState.SUBMITTING)
            self.assertEqual(observer.reserved(intent.account_id, "QUOTE"), Decimal("21"))
            self.assertEqual([e.kind for e in observer.events()], ["intent_planned", "order_submitting"])
            return actual_submit(intent)
        with patch.object(source, "submit", observed):
            result = gateway.submit(self.request)
        self.assertEqual(result.receipt.state, OrderState.WORKING)
        self.assertEqual(result.receipt.cumulative_filled_quantity, Decimal(0))

    def test_lost_response_reconciles_original_identity_without_duplicate_submission(self):
        gateway, source = self.gateway(loss=True)
        first = gateway.submit(self.request)
        self.assertEqual(first.receipt.state, OrderState.UNKNOWN)
        self.assertIsNone(first.receipt.cumulative_filled_quantity)
        self.assertTrue(first.receipt.reservation_held)
        repeated = gateway.submit(self.request)
        self.assertEqual(repeated.receipt.state, OrderState.UNKNOWN)
        with self.assertRaises(ExecutionRefused):
            gateway.submit(synthetic_request(intent_id="new-intent", client_id="new-client"))
        receipt = gateway.reconcile(self.request.intent.intent_id)
        self.assertEqual(receipt.state, OrderState.WORKING)
        self.assertEqual(receipt.last_observation.source_order_id, "fixture-order-1")
        self.assertEqual(receipt.client_id, self.request.intent.client_id)
        self.assertEqual(source.submit_calls, [self.key])

    def test_not_found_retains_unknown_reservation_and_never_authorizes_a_new_id(self):
        gateway, source = self.gateway(loss=True, lookups={self.key: [None]})
        gateway.submit(self.request)
        missing = gateway.reconcile(self.request.intent.intent_id)
        self.assertEqual(missing.state, OrderState.UNKNOWN)
        self.assertEqual(missing.uncertainty_reason, "not_found_does_not_prove_nonacceptance")
        self.assertEqual(self.ledger.reserved(self.key[0], "QUOTE"), Decimal("21"))
        with self.assertRaises(ExecutionRefused):
            gateway.submit(synthetic_request(intent_id="new", client_id="new"))
        self.assertEqual(gateway.reconcile(self.request.intent.intent_id).state, OrderState.WORKING)
        self.assertEqual(len(source.submit_calls), 1)

    def test_restart_requires_explicit_recovery_and_keeps_the_same_client(self):
        gateway, source = self.gateway(loss=True)
        gateway.submit(self.request)
        restarted = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        with self.assertRaises(RecoveryRequired):
            restarted.submit(synthetic_request(intent_id="new", client_id="new"))
        self.assertEqual(restarted.submit(self.request).disposition, "reconciliation_required")
        receipts = restarted.recover_account(self.key[0], run_id="restart-run", trace_id="restart-trace")
        self.assertEqual(receipts[0].state, OrderState.WORKING)
        self.assertEqual(source.lookup_calls, [self.key])
        self.assertEqual(source.submit_calls, [self.key])
        self.assertIsNone(receipts[0].sellable_quantity_base)

    def test_restart_from_submitting_before_any_durable_receipt_never_resubmits(self):
        record, _ = self.ledger.plan(self.request, at=FIXTURE_TIME)
        self.ledger.transition(self.request.intent.intent_id, OrderState.SUBMITTING,
                               expected_version=record.version, at=FIXTURE_TIME, reason="crashed_after_send")
        source = ReplayTransport({}, lookups={self.key: [synthetic_observation()]})
        restarted = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        receipts = restarted.recover_account(self.key[0], run_id="restart", trace_id="restart")
        self.assertEqual(receipts[0].state, OrderState.WORKING)
        self.assertEqual(source.submit_calls, [])

    def test_two_gateway_instances_cannot_dispatch_the_same_account_concurrently(self):
        gateway, source = self.gateway()
        other = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        entered, release = Event(), Event()
        actual = source.submit
        def slow(intent):
            entered.set()
            self.assertTrue(release.wait(5))
            return actual(intent)
        with patch.object(source, "submit", slow), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(gateway.submit, self.request)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(WriterBusy):
                    other.submit(self.request)
            finally:
                release.set()
            self.assertEqual(pending.result().receipt.state, OrderState.WORKING)
        self.assertEqual(source.submit_calls, [self.key])

    def test_audit_failure_before_submitting_prevents_transport_call(self):
        gateway, source = self.gateway()
        self.trigger("order_submitting")
        with self.assertRaises(StorageError):
            gateway.submit(self.request)
        self.assertEqual(source.submit_calls, [])
        self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.PLANNED)

    def test_audit_failure_after_acceptance_preserves_submitting_for_reconciliation(self):
        gateway, source = self.gateway()
        self.trigger("order_observed")
        with self.assertRaises(StorageError):
            gateway.submit(self.request)
        self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.SUBMITTING)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER fail_audit")
            db.commit()
        restarted = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        self.assertEqual(restarted.recover_account(self.key[0], run_id="r", trace_id="t")[0].state,
                         OrderState.WORKING)
        self.assertEqual(len(source.submit_calls), 1)

    def test_invalid_source_identity_becomes_unknown_and_cannot_overwrite_identity(self):
        source = ReplayTransport({self.key: ReplayScenario(replace(synthetic_observation(), client_id="other"))})
        gateway = ReplayGateway(self.ledger, source, clock=lambda: FIXTURE_TIME)
        result = gateway.submit(self.request)
        self.assertEqual(result.receipt.state, OrderState.UNKNOWN)
        self.assertEqual(result.receipt.client_id, self.request.intent.client_id)
        self.assertIsNone(result.receipt.last_observation)

    def test_shadow_live_and_actual_no_trade_never_submit_or_reserve(self):
        gateway, source = self.gateway()
        shadow = gateway.submit(synthetic_request(mode=Mode.SHADOW))
        self.assertEqual(shadow.disposition, "shadow_recorded")
        self.assertIsNone(shadow.receipt)
        with self.assertRaises(LiveUnavailable):
            gateway.submit(synthetic_request(mode=Mode.LIVE))
        decision = evaluate(StrategyInput("FIXTURE-PAIR", (Decimal("10"),)))
        result = gateway.consume_strategy_decision(decision, account_id=self.key[0],
                                                   run_id="fixture-run", trace_id="fixture-trace")
        self.assertEqual(result.disposition, "no_trade")
        self.assertEqual(self.ledger.records(self.key[0]), ())
        self.assertEqual(self.ledger.reserved(self.key[0], "QUOTE"), Decimal(0))
        self.assertEqual(source.submit_calls, [])
        self.assertEqual([e.kind for e in self.ledger.events()],
                         ["shadow_decision_recorded", "live_refused", "strategy_no_trade"])

    def test_pause_entries_does_not_disable_reconciliation_or_cancellation(self):
        canceled = synthetic_observation(state=OrderState.CANCELED)
        gateway, source = self.gateway(loss=True, cancellation=canceled)
        gateway.submit(self.request)
        gateway.stop_new_entries = True
        self.assertEqual(gateway.reconcile(self.request.intent.intent_id).state, OrderState.WORKING)
        result = gateway.cancel(self.request.intent.intent_id)
        self.assertEqual(result.state, OrderState.CANCELED)
        self.assertTrue(result.reservation_held)
        self.assertEqual(source.cancel_calls, [self.key])

    def test_conflicting_duplicate_and_expired_request_do_not_submit(self):
        gateway, source = self.gateway()
        gateway.submit(self.request)
        with self.assertRaises(IdentityConflict):
            gateway.submit(replace(self.request, intent=replace(self.request.intent, quantity_base=Decimal("1"))))
        expired_request = synthetic_request(intent_id="expired-intent", client_id="expired-client",
                                            account_id="separate-expired-account")
        expired = ReplayGateway(self.ledger, source, clock=lambda: expired_request.intent.expires_at)
        result = expired.submit(expired_request)
        self.assertEqual(result.disposition, "expired_without_submission")
        self.assertEqual(result.receipt.state, OrderState.REJECTED)
        self.assertFalse(result.receipt.reservation_held)
        self.assertEqual(len(source.submit_calls), 1)


if __name__ == "__main__":
    unittest.main()
