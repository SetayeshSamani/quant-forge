from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from wallex.audit.ledger import IdentityConflict, Ledger, LedgerError, ReservationExceeded, StorageError
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_request
from wallex.execution.models import ContractError, Mode, OrderState, Reservation


class IntentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)

    def test_atomic_plan_persists_all_references_and_survives_reopen(self):
        request = synthetic_request()
        record, created = self.ledger.plan(request, at=FIXTURE_TIME)
        self.assertTrue(created)
        self.assertEqual(record.state, OrderState.PLANNED)
        self.assertEqual(record.request, request)
        self.assertEqual(self.ledger.reserved(request.intent.account_id, "QUOTE"), Decimal("21"))
        event = self.ledger.events(request.intent.intent_id)[0]
        self.assertEqual(event.kind, "intent_planned")
        self.assertEqual(event.trace_id, request.intent.trace_id)
        self.assertEqual(event.payload["fingerprint"], request.fingerprint)
        self.assertEqual(event.payload["references"]["risk_decision_id"], request.references.risk_decision_id)
        reopened = Ledger(self.path)
        self.assertEqual(reopened.get(request.intent.intent_id), record)
        self.assertEqual(reopened.events(), self.ledger.events())
        self.assertIsNone(record.reported_filled_quantity)

    def test_identical_duplicates_do_not_duplicate_audits_or_reservations(self):
        request = synthetic_request()
        first, _ = self.ledger.plan(request, at=FIXTURE_TIME)
        second, created = self.ledger.plan(request, at=FIXTURE_TIME)
        self.assertFalse(created)
        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger.events()), 1)
        self.assertEqual(self.ledger.reserved(request.intent.account_id, "QUOTE"), Decimal("21"))

    def test_conflicts_for_intent_client_payload_account_and_version_references(self):
        request = synthetic_request()
        self.ledger.plan(request, at=FIXTURE_TIME)
        cases = [replace(request, intent=replace(request.intent, client_id="another")),
                 replace(request, intent=replace(request.intent, intent_id="another")),
                 replace(request, intent=replace(request.intent, account_id="another")),
                 replace(request, intent=replace(request.intent, quantity_base=Decimal("1"))),
                 replace(request, references=replace(request.references, risk_policy_version="v2")),
                 replace(request, reservation=replace(request.reservation, amount=Decimal("22")))]
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(IdentityConflict):
                self.ledger.plan(changed, at=FIXTURE_TIME)
        self.assertEqual(len(self.ledger.records(request.intent.account_id)), 1)

    def test_competing_ledger_connections_cannot_overbook_explicit_capacity(self):
        ledgers = (self.ledger, Ledger(self.path))
        requests = [replace(synthetic_request(intent_id=f"i-{n}", client_id=f"c-{n}"),
                            reservation=Reservation("QUOTE", Decimal("21"), Decimal("30"))) for n in range(2)]
        def admit(index):
            try:
                return ledgers[index].plan(requests[index], at=FIXTURE_TIME)[1]
            except ReservationExceeded:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(admit, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.ledger.reserved("fixture-account", "QUOTE"), Decimal("21"))

    def test_audit_failure_rolls_back_intent_and_reservation_together(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events BEGIN "
                       "SELECT RAISE(ABORT, 'synthetic audit failure'); END")
            db.commit()
        with self.assertRaises(StorageError):
            self.ledger.plan(synthetic_request(), at=FIXTURE_TIME)
        self.assertEqual(self.ledger.records("fixture-account"), ())
        self.assertEqual(self.ledger.reserved("fixture-account", "QUOTE"), Decimal(0))
        self.assertEqual(self.ledger.events(), ())

    def test_risk_denials_shadow_and_live_cannot_create_replay_orders(self):
        request = synthetic_request()
        for changed in (replace(request, risk_decision=replace(request.risk_decision, allowed=False)),
                        synthetic_request(mode=Mode.SHADOW), synthetic_request(mode=Mode.LIVE)):
            with self.assertRaises(ContractError):
                self.ledger.plan(changed, at=FIXTURE_TIME)
        self.assertEqual(self.ledger.records("fixture-account"), ())

    def test_unrelated_database_is_not_replaced_or_extended(self):
        unrelated = Path(self.directory.name) / "unrelated.sqlite3"
        with closing(sqlite3.connect(unrelated)) as db:
            db.execute("CREATE TABLE original(value TEXT)")
            db.execute("INSERT INTO original VALUES ('preserve')")
            db.commit()
        with self.assertRaises(LedgerError):
            Ledger(unrelated)
        with closing(sqlite3.connect(unrelated)) as db:
            self.assertEqual(db.execute("SELECT * FROM original").fetchall(), [("preserve",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [("original",)])


if __name__ == "__main__":
    unittest.main()
