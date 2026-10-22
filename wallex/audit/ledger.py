"""SQLite intent/reservation/audit commits. No transaction spans external I/O."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from wallex.execution.models import (
    ContractError, ExecutionRequest, Mode, OrderState, canonical_json, decimal_text,
    exact_sum, identifier, json_value, utc,
)
from wallex.risk import RiskReason

from .models import AUDIT_SCHEMA_VERSION, AuditEvent


class LedgerError(RuntimeError):
    pass


class StorageError(LedgerError):
    pass


class IdentityConflict(LedgerError):
    pass


class ReservationExceeded(LedgerError):
    pass


@dataclass(frozen=True)
class IntentRecord:
    request: ExecutionRequest
    state: OrderState
    version: int
    reservation_held: bool
    source_order_id: str | None
    reported_filled_quantity: Decimal | None
    uncertainty_reason: str | None
    last_observed_at: datetime | None


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS intents (
        intent_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, client_id TEXT NOT NULL,
        symbol TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode='replay'),
        fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PLANNED','SUBMITTING','WORKING','PARTIAL',
            'CANCEL_PENDING','UNKNOWN','RECONCILING','FILLED','CANCELED','REJECTED')),
        version INTEGER NOT NULL DEFAULT 0,
        reservation_asset TEXT NOT NULL, reservation_amount TEXT NOT NULL,
        reservation_held INTEGER NOT NULL CHECK(reservation_held IN (0,1)),
        source_order_id TEXT, reported_filled_quantity TEXT, uncertainty_reason TEXT,
        last_observed_at TEXT,
        UNIQUE(account_id, client_id))""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        event_seq INTEGER PRIMARY KEY AUTOINCREMENT, schema_version TEXT NOT NULL,
        kind TEXT NOT NULL, intent_id TEXT, account_id TEXT NOT NULL,
        run_id TEXT NOT NULL, trace_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS fills (
        account_id TEXT NOT NULL, source TEXT NOT NULL, fill_id TEXT NOT NULL,
        intent_id TEXT NOT NULL REFERENCES intents(intent_id), payload_json TEXT NOT NULL,
        PRIMARY KEY(account_id, source, fill_id))""",
)


class Ledger:
    """One canonical local DB path, full-sync commits and short write transactions.

    The DB must be dedicated to this module; unrelated databases and unknown
    schema versions are refused. No existing database is replaced or migrated.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if tables and "ledger_meta" not in tables:
                raise LedgerError("Refusing an unrelated database.")
            if "ledger_meta" in tables:
                row = db.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
                if row is None or row[0] != AUDIT_SCHEMA_VERSION:
                    raise LedgerError("Unsupported ledger schema; no automatic migration is available.")
            for statement in _SCHEMA:
                db.execute(statement)
            db.execute("INSERT OR IGNORE INTO ledger_meta VALUES ('schema_version', ?)",
                       (AUDIT_SCHEMA_VERSION,))

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error:
            if db is not None:
                db.rollback()
            raise StorageError("Ledger commit/read failed; submission must not advance.") from None
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _record(row) -> IntentRecord:
        return IntentRecord(
            ExecutionRequest.from_dict(json.loads(row["request_json"])), OrderState(row["state"]),
            row["version"], bool(row["reservation_held"]), row["source_order_id"],
            Decimal(row["reported_filled_quantity"]) if row["reported_filled_quantity"] is not None else None,
            row["uncertainty_reason"],
            datetime.fromisoformat(row["last_observed_at"]) if row["last_observed_at"] else None,
        )

    def get(self, intent_id: str) -> IntentRecord | None:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (identifier(intent_id),)).fetchone()
            return self._record(row) if row else None

    def records(self, account_id: str) -> tuple[IntentRecord, ...]:
        with self._transaction() as db:
            rows = db.execute("SELECT * FROM intents WHERE account_id=? ORDER BY intent_id",
                              (identifier(account_id),)).fetchall()
            return tuple(self._record(row) for row in rows)

    @staticmethod
    def _reserved(db, account_id: str, asset: str) -> Decimal:
        return exact_sum(Decimal(row[0]) for row in db.execute(
            "SELECT reservation_amount FROM intents WHERE account_id=? AND reservation_asset=? "
            "AND reservation_held=1", (account_id, asset)
        ))

    def reserved(self, account_id: str, asset: str) -> Decimal:
        with self._transaction() as db:
            return self._reserved(db, identifier(account_id), identifier(asset))

    @staticmethod
    def _event(db, kind: str, *, account_id: str, run_id: str, trace_id: str,
               at: datetime, payload: dict, intent_id: str | None = None) -> str:
        for value in (kind, account_id, run_id, trace_id):
            identifier(value)
        if intent_id is not None:
            identifier(intent_id)
        cursor = db.execute(
            "INSERT INTO audit_events(schema_version,kind,intent_id,account_id,run_id,trace_id,"
            "occurred_at,payload_json) VALUES (?,?,?,?,?,?,?,?)",
            (AUDIT_SCHEMA_VERSION, kind, intent_id, account_id, run_id, trace_id,
             json_value(utc(at)), canonical_json(payload)),
        )
        return f"audit-{cursor.lastrowid}"

    def record_event(self, kind: str, *, account_id: str, run_id: str, trace_id: str,
                     at: datetime, payload: dict, intent_id: str | None = None) -> str:
        with self._transaction() as db:
            return self._event(db, kind, account_id=account_id, run_id=run_id, trace_id=trace_id,
                               at=at, payload=payload, intent_id=intent_id)

    def events(self, intent_id: str | None = None) -> tuple[AuditEvent, ...]:
        with self._transaction() as db:
            query = "SELECT * FROM audit_events"
            rows = db.execute(query + (" WHERE intent_id=?" if intent_id else "") + " ORDER BY event_seq",
                              (intent_id,) if intent_id else ()).fetchall()
            return tuple(AuditEvent(f"audit-{row['event_seq']}", row["schema_version"], row["kind"],
                                    row["intent_id"], row["account_id"], row["run_id"], row["trace_id"],
                                    datetime.fromisoformat(row["occurred_at"]), row["payload_json"])
                         for row in rows)

    def plan(self, request: ExecutionRequest, *, at: datetime) -> tuple[IntentRecord, bool]:
        """Atomically persist identity, payload, reservation, state and first audit.

        Return (record, created). Reusing either identity with changed canonical
        data is a conflict. Capacity checks serialize with the same write commit.
        """
        if not isinstance(request, ExecutionRequest):
            raise ContractError("Expected the typed execution consumer request.")
        request = ExecutionRequest.from_dict(request.to_dict())
        if (request.intent.mode != Mode.REPLAY or request.risk_decision.allowed is not True
                or request.risk_decision.reason != RiskReason.WITHIN_LIMITS):
            raise ContractError("Only validated synthetic replay requests can reserve an order.")
        intent, reservation = request.intent, request.reservation
        encoded, fingerprint = canonical_json(request), request.fingerprint
        with self._transaction() as db:
            rows = db.execute(
                "SELECT * FROM intents WHERE intent_id=? OR (account_id=? AND client_id=?)",
                (intent.intent_id, intent.account_id, intent.client_id),
            ).fetchall()
            if rows:
                if len(rows) != 1 or rows[0]["fingerprint"] != fingerprint or rows[0]["request_json"] != encoded:
                    raise IdentityConflict("Intent/client identity already belongs to a different canonical request.")
                return self._record(rows[0]), False
            total = exact_sum((self._reserved(db, intent.account_id, reservation.asset), reservation.amount))
            if total > reservation.capacity_limit:
                raise ReservationExceeded("Aggregate held reservation exceeds the explicit supplied capacity.")
            db.execute(
                "INSERT INTO intents(intent_id,account_id,client_id,symbol,mode,fingerprint,request_json,"
                "state,reservation_asset,reservation_amount,reservation_held,uncertainty_reason) "
                "VALUES (?,?,?,?,?,?,?,'PLANNED',?,?,1,'no_venue_observation')",
                (intent.intent_id, intent.account_id, intent.client_id, intent.symbol, intent.mode.value,
                 fingerprint, encoded, reservation.asset, decimal_text(reservation.amount)),
            )
            self._event(db, "intent_planned", account_id=intent.account_id, run_id=intent.run_id,
                        trace_id=intent.trace_id, at=at, intent_id=intent.intent_id,
                        payload={"fingerprint": fingerprint, "client_id": intent.client_id,
                                 "symbol": intent.symbol, "mode": intent.mode,
                                 "reservation": reservation, "references": request.references})
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (intent.intent_id,)).fetchone()
            return self._record(row), True
