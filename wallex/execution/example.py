"""Run a temporary, explicitly synthetic lost-response/restart example offline."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from wallex.audit.ledger import Ledger

from .fixtures import FIXTURE_TIME, synthetic_observation, synthetic_request
from .gateway import RecoveryRequired, ReplayGateway
from .models import OrderState
from .replay import ReplayScenario, ReplayTransport


def run_example() -> dict:
    request = synthetic_request()
    intent = request.intent
    key = (intent.account_id, intent.client_id)
    # This fixture represents a venue accepting the original order and losing
    # its response. Reusing it after gateway restart supplies synthetic evidence;
    # it is not durable venue storage or a real account connection.
    source = ReplayTransport({key: ReplayScenario(
        synthetic_observation(request), lose_response_after_accept=True,
    )})
    with TemporaryDirectory(prefix="quantforge-synthetic-") as directory:
        path = Path(directory) / "intent-audit.sqlite3"
        ledger = Ledger(path)
        gateway = ReplayGateway(ledger, source, clock=lambda: FIXTURE_TIME)
        uncertain = gateway.submit(request)
        repeated = gateway.submit(request)
        restarted = ReplayGateway(Ledger(path), source, clock=lambda: FIXTURE_TIME)
        before_recovery = restarted.submit(request)
        blocked = False
        try:
            restarted.submit(synthetic_request(intent_id="fixture-intent-2", client_id="fixture-client-2"))
        except RecoveryRequired:
            blocked = True
        recovered = restarted.recover_account(
            intent.account_id, run_id="fixture-restart-run", trace_id="fixture-restart-trace",
        )[0]
        if not (uncertain.receipt.state == OrderState.UNKNOWN
                and recovered.state == OrderState.WORKING
                and recovered.client_id == intent.client_id
                and blocked and source.submit_calls == [key]
                and source.lookup_calls == [key] and recovered.reservation_held):
            raise AssertionError("Synthetic uncertainty/recovery invariant failed")
        return {
            "evidence_kind": "synthetic_fixture",
            "live_trading_enabled": False,
            "unknown_result": uncertain.to_dict(),
            "duplicate_disposition": repeated.disposition,
            "restart_disposition": before_recovery.disposition,
            "new_entry_blocked_before_recovery": blocked,
            "recovered_receipt": recovered.to_dict(),
            "transport_submit_count": len(source.submit_calls),
            "transport_lookup_count": len(source.lookup_calls),
            "held_reservation_quote": str(ledger.reserved(intent.account_id, intent.quote_asset)),
            "account_balances_reconciled": False,
            "audit_kinds": [event.kind for event in ledger.events()],
        }


if __name__ == "__main__":
    print(json.dumps(run_example(), indent=2, allow_nan=False))
