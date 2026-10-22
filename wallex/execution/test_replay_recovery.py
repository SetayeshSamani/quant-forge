from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wallex.audit.ledger import Ledger, StorageError
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_observation, synthetic_request
from wallex.execution.gateway import ReplayGateway
from wallex.execution.models import OrderObservation, OrderState
from wallex.execution.replay import ReplayScenario, ReplayTransport


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.request = synthetic_request()
        self.key = (self.request.intent.account_id, self.request.intent.client_id)

    def test_hard_process_exit_after_acceptance_recovers_from_explicit_venue_evidence(self):
        evidence_path = Path(self.directory.name) / "synthetic-venue-evidence.json"
        script = """
import json, os, sys
from pathlib import Path
from wallex.audit.ledger import Ledger
from wallex.execution.fixtures import FIXTURE_TIME, synthetic_request, synthetic_observation
from wallex.execution.models import json_value
from wallex.execution.gateway import ReplayGateway
from wallex.execution.replay import ReplayTransport, ReplayScenario
request = synthetic_request()
key = (request.intent.account_id, request.intent.client_id)
class CrashAfterSyntheticAcceptance(ReplayTransport):
    def submit(self, intent):
        accepted = super().submit(intent)
        with Path(sys.argv[2]).open('w', encoding='utf-8') as stream:
            json.dump(json_value(accepted), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(23)
source = CrashAfterSyntheticAcceptance({key: ReplayScenario(synthetic_observation())})
ReplayGateway(Ledger(sys.argv[1]), source, clock=lambda: FIXTURE_TIME).submit(request)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(self.path), str(evidence_path)], timeout=15)
        self.assertEqual(result.returncode, 23)
        reopened = Ledger(self.path)
        self.assertEqual(reopened.get(self.request.intent.intent_id).state, OrderState.SUBMITTING)
        self.assertEqual([e.kind for e in reopened.events()], ["intent_planned", "order_submitting"])
        observation = OrderObservation.from_dict(json.loads(evidence_path.read_text(encoding="utf-8")))
        source = ReplayTransport({}, lookups={self.key: [observation]})
        restarted = ReplayGateway(reopened, source, clock=lambda: FIXTURE_TIME)
        self.assertEqual(restarted.submit(self.request).disposition, "reconciliation_required")
        receipt = restarted.recover_account(self.key[0], run_id="restart-run", trace_id="restart-trace")[0]
        self.assertEqual(receipt.state, OrderState.WORKING)
        self.assertEqual(receipt.client_id, self.request.intent.client_id)
        self.assertEqual(source.submit_calls, [])
        self.assertEqual(source.lookup_calls, [self.key])
        self.assertTrue(receipt.reservation_held)

    def test_corrupted_stored_identity_is_refused_before_any_recovery_transport(self):
        self.ledger.plan(self.request, at=FIXTURE_TIME)
        payload = self.request.to_dict()
        payload["intent"]["client_id"] = "corrupted-client"
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE intents SET request_json=?", (json.dumps(payload),))
            db.commit()
        source = ReplayTransport({})
        gateway = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        with self.assertRaises(StorageError):
            gateway.recover_account(self.key[0], run_id="restart", trace_id="restart")
        self.assertEqual(source.lookup_calls, [])
        self.assertEqual(source.submit_calls, [])

    def test_failed_unknown_audit_after_lost_response_leaves_submitting_recoverable(self):
        source = ReplayTransport({self.key: ReplayScenario(synthetic_observation(), True)})
        gateway = ReplayGateway(self.ledger, source, clock=lambda: FIXTURE_TIME)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_unknown BEFORE INSERT ON audit_events "
                       "WHEN NEW.kind='order_unknown' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
            db.commit()
        with self.assertRaises(StorageError):
            gateway.submit(self.request)
        self.assertEqual(self.ledger.get(self.request.intent.intent_id).state, OrderState.SUBMITTING)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER fail_unknown")
            db.commit()
        restarted = ReplayGateway(Ledger(self.path), source, clock=lambda: FIXTURE_TIME)
        result = restarted.recover_account(self.key[0], run_id="r", trace_id="t")[0]
        self.assertEqual(result.state, OrderState.WORKING)
        self.assertEqual(source.submit_calls, [self.key])

    def test_full_uncertainty_flow_is_offline_and_preserves_serialized_unknowns(self):
        source = ReplayTransport({self.key: ReplayScenario(synthetic_observation(), True)})
        gateway = ReplayGateway(self.ledger, source, clock=lambda: FIXTURE_TIME)
        def forbidden(*args, **kwargs):
            raise AssertionError("Replay must never use network or order APIs")
        with patch("socket.socket", forbidden), patch("socket.create_connection", forbidden), \
                patch("urllib.request.urlopen", forbidden), patch("urllib.request.build_opener", forbidden):
            first = gateway.submit(self.request).to_dict()
            final = gateway.reconcile(self.request.intent.intent_id).to_dict()
        self.assertEqual(first["receipt"]["state"], "UNKNOWN")
        self.assertIsNone(first["receipt"]["cumulative_filled_quantity"])
        self.assertIsNone(first["receipt"]["remaining_quantity_base"])
        self.assertEqual(final["state"], "WORKING")
        self.assertEqual(final["mode"], "replay")
        self.assertEqual(final["reconciliation_scope"], "synthetic_order_only")
        self.assertIsNone(final["sellable_quantity_base"])
        self.assertNotIn("account_balances", final)
        json.dumps(final, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
