from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from wallex.audit.ledger import Ledger, StateConflict, StorageError
from wallex.audit.writer import WriterBusy
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_request
from wallex.execution.models import OrderState


class DurableWriterTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.request = synthetic_request()

    def test_control_state_and_audit_survive_restart_and_keep_reservation(self):
        first, _ = self.ledger.plan(self.request, at=FIXTURE_TIME)
        submitting = self.ledger.transition(first.request.intent.intent_id, OrderState.SUBMITTING,
                                            expected_version=first.version, at=FIXTURE_TIME,
                                            reason="submission_acceptance_unknown")
        reopened = Ledger(self.path)
        self.assertEqual(reopened.get(self.request.intent.intent_id), submitting)
        self.assertTrue(submitting.reservation_held)
        unknown = reopened.transition(self.request.intent.intent_id, OrderState.UNKNOWN,
                                      expected_version=submitting.version, at=FIXTURE_TIME,
                                      reason="restarted_before_receipt")
        self.assertEqual(unknown.request.intent.client_id, self.request.intent.client_id)
        self.assertTrue(unknown.reservation_held)
        self.assertEqual([e.kind for e in reopened.events()],
                         ["intent_planned", "order_submitting", "order_unknown"])

    def test_version_conflicts_and_unsupported_transitions_refuse_advancement(self):
        record, _ = self.ledger.plan(self.request, at=FIXTURE_TIME)
        record = self.ledger.transition(self.request.intent.intent_id, OrderState.SUBMITTING,
                                       expected_version=record.version, at=FIXTURE_TIME, reason="submitting")
        for state, version in ((OrderState.SUBMITTING, record.version), (OrderState.UNKNOWN, 0),
                               (OrderState.FILLED, record.version)):
            with self.assertRaises(StateConflict):
                self.ledger.transition(self.request.intent.intent_id, state, expected_version=version,
                                       at=FIXTURE_TIME, reason="invalid_attempt")
        with self.assertRaises(StateConflict):
            self.ledger.reject_unsubmitted(self.request.intent.intent_id, expected_version=record.version,
                                           at=FIXTURE_TIME, reason="cannot_prove_not_submitted")
        self.assertTrue(self.ledger.get(self.request.intent.intent_id).reservation_held)

    def test_failed_audit_rolls_back_submitting_transition(self):
        record, _ = self.ledger.plan(self.request, at=FIXTURE_TIME)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_submitting BEFORE INSERT ON audit_events "
                       "WHEN NEW.kind='order_submitting' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
            db.commit()
        with self.assertRaises(StorageError):
            self.ledger.transition(self.request.intent.intent_id, OrderState.SUBMITTING,
                                   expected_version=record.version, at=FIXTURE_TIME, reason="submitting")
        self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.PLANNED)
        self.assertEqual(len(self.ledger.events()), 1)

    def test_per_account_lock_excludes_another_instance_and_process(self):
        other = Ledger(self.path)
        with self.ledger.writer("fixture-account"):
            with self.assertRaises(WriterBusy):
                with other.writer("fixture-account"):
                    self.fail("Second writer acquired the same account")
            script = """
import sys
from pathlib import Path
from wallex.audit.writer import account_writer, WriterBusy
try:
    with account_writer(Path(sys.argv[1]), 'fixture-account'):
        raise AssertionError('Second process acquired the writer')
except WriterBusy:
    print('busy')
"""
            result = subprocess.run([sys.executable, "-B", "-c", script, str(self.path)],
                                    capture_output=True, text=True, check=True, timeout=15)
            self.assertEqual(result.stdout.strip(), "busy")
            with other.writer("different-account"):
                pass
        with other.writer("fixture-account"):
            pass

    def test_process_death_releases_lock_but_retains_durable_uncertainty(self):
        record, _ = self.ledger.plan(self.request, at=FIXTURE_TIME)
        self.ledger.transition(self.request.intent.intent_id, OrderState.SUBMITTING,
                               expected_version=record.version, at=FIXTURE_TIME, reason="before_crash")
        script = """
import os, sys
from pathlib import Path
from wallex.audit.writer import account_writer
with account_writer(Path(sys.argv[1]), 'fixture-account'):
    os._exit(17)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(self.path)], timeout=15)
        self.assertEqual(result.returncode, 17)
        with Ledger(self.path).writer("fixture-account"):
            self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.SUBMITTING)


if __name__ == "__main__":
    unittest.main()
