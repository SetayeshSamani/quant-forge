"""SQLite intent/reservation/audit commits. No transaction spans external I/O."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from wallex.execution.models import (
    ContractError, ExecutionReceipt, ExecutionRequest, Fill, Mode, OrderObservation, OrderState, Side,
    canonical_json, decimal_text,
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


class StateConflict(LedgerError):
    pass


class ObservationError(LedgerError):
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

    def writer(self, account_id: str):
        from .writer import account_writer
        return account_writer(self.path, account_id)

    def transition(self, intent_id: str, target: OrderState, *, expected_version: int,
                   at: datetime, reason: str) -> IntentRecord:
        """Only control states; receipt states require source observations separately."""
        allowed = {
            OrderState.SUBMITTING: {OrderState.PLANNED},
            OrderState.CANCEL_PENDING: {OrderState.WORKING, OrderState.PARTIAL},
            OrderState.RECONCILING: set(OrderState),
            OrderState.UNKNOWN: {OrderState.SUBMITTING, OrderState.CANCEL_PENDING,
                                 OrderState.RECONCILING, OrderState.UNKNOWN},
        }
        if not isinstance(target, OrderState) or target not in allowed or type(expected_version) is not int:
            raise StateConflict("Unsupported control transition or version.")
        identifier(reason)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (identifier(intent_id),)).fetchone()
            if row is None or row["version"] != expected_version or OrderState(row["state"]) not in allowed[target]:
                raise StateConflict("Order state/version changed; do not perform an external action.")
            record = self._record(row)
            db.execute("UPDATE intents SET state=?, version=version+1, uncertainty_reason=? "
                       "WHERE intent_id=? AND version=?",
                       (target.value, reason, intent_id, expected_version))
            intent = record.request.intent
            self._event(db, f"order_{target.value.lower()}", account_id=intent.account_id,
                        run_id=intent.run_id, trace_id=intent.trace_id, at=at, intent_id=intent_id,
                        payload={"from": record.state, "to": target, "reason": reason,
                                 "version": expected_version + 1})
            return self._record(db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone())

    def reject_unsubmitted(self, intent_id: str, *, expected_version: int, at: datetime,
                           reason: str) -> IntentRecord:
        """Only a never-submitted PLANNED record can release its reservation here."""
        identifier(reason)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (identifier(intent_id),)).fetchone()
            if row is None or row["state"] != OrderState.PLANNED.value or row["version"] != expected_version:
                raise StateConflict("Cannot release a submitted or changed order reservation.")
            record = self._record(row)
            db.execute("UPDATE intents SET state='REJECTED', version=version+1, reservation_held=0, "
                       "uncertainty_reason='never_submitted' WHERE intent_id=?", (intent_id,))
            intent = record.request.intent
            self._event(db, "unsubmitted_rejected", account_id=intent.account_id, run_id=intent.run_id,
                        trace_id=intent.trace_id, at=at, intent_id=intent_id, payload={"reason": reason})
            return self._record(db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone())

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

    @classmethod
    def _matching(cls, db, request: ExecutionRequest) -> IntentRecord | None:
        intent = request.intent
        rows = db.execute("SELECT * FROM intents WHERE intent_id=? OR (account_id=? AND client_id=?)",
                          (intent.intent_id, intent.account_id, intent.client_id)).fetchall()
        if not rows:
            return None
        if (len(rows) != 1 or rows[0]["fingerprint"] != request.fingerprint
                or rows[0]["request_json"] != canonical_json(request)):
            raise IdentityConflict("Intent/client identity already belongs to a different canonical request.")
        return cls._record(rows[0])

    def existing(self, request: ExecutionRequest) -> IntentRecord | None:
        with self._transaction() as db:
            return self._matching(db, request)

    @staticmethod
    def _last_observation(db, intent_id: str) -> OrderObservation | None:
        row = db.execute("SELECT payload_json FROM audit_events WHERE intent_id=? "
                         "AND kind='order_observed' ORDER BY event_seq DESC LIMIT 1", (intent_id,)).fetchone()
        return OrderObservation.from_dict(json.loads(row[0])["observation"]) if row else None

    @staticmethod
    def _fills(db, intent_id: str) -> tuple[Fill, ...]:
        return tuple(Fill.from_dict(json.loads(row[0])) for row in db.execute(
            "SELECT payload_json FROM fills WHERE intent_id=? ORDER BY source,fill_id", (intent_id,)
        ))

    def fills(self, intent_id: str) -> tuple[Fill, ...]:
        with self._transaction() as db:
            return self._fills(db, identifier(intent_id))

    def _merge_fills(self, db, record: IntentRecord, observation: OrderObservation, at: datetime):
        intent = record.request.intent
        for fill in observation.fills:
            if (fill.account_id, fill.client_id, fill.symbol, fill.base_asset, fill.quote_asset,
                    fill.source_order_id, fill.source) != (
                    intent.account_id, intent.client_id, intent.symbol, intent.base_asset, intent.quote_asset,
                    observation.source_order_id, observation.source):
                raise ObservationError("Fill identity or units do not match this order/source.")
            if intent.side == Side.BUY and fill.price_quote_per_base > intent.price_quote_per_base:
                raise ObservationError("Fill price violates the persisted buy limit.")
            if fill.fee_asset == intent.base_asset and fill.fee_amount > fill.quantity_base:
                raise ObservationError("Base fee exceeds this fixture's acquired fill quantity.")
            row = db.execute("SELECT intent_id,payload_json FROM fills WHERE account_id=? AND source=? AND fill_id=?",
                             (intent.account_id, fill.source, fill.fill_id)).fetchone()
            encoded = json_value(fill)
            if row is not None:
                previous = json.loads(row["payload_json"])
                # Delivery time may change when the same stable economic event
                # is redelivered. Preserve the first receipt and never recount it.
                previous.pop("received_at")
                incoming = {key: value for key, value in encoded.items() if key != "received_at"}
                if row["intent_id"] != intent.intent_id or previous != incoming:
                    raise ObservationError("Stable Fill identity was reused with conflicting content.")
                continue
            db.execute("INSERT INTO fills(account_id,source,fill_id,intent_id,payload_json) VALUES (?,?,?,?,?)",
                       (intent.account_id, fill.source, fill.fill_id, intent.intent_id, canonical_json(fill)))
            self._event(db, "fill_recorded", account_id=intent.account_id, run_id=intent.run_id,
                        trace_id=intent.trace_id, at=at, intent_id=intent.intent_id, payload={"fill": fill})
        quantity = exact_sum(fill.quantity_base for fill in self._fills(db, intent.intent_id))
        if quantity != observation.cumulative_filled_quantity:
            raise ObservationError("Cumulative quantity lacks complete, deduplicated Fill evidence.")

    def observe(self, intent_id: str, observation: OrderObservation, *, expected_version: int,
                at: datetime) -> IntentRecord:
        if not isinstance(observation, OrderObservation):
            raise ObservationError("Expected a typed synthetic order observation.")
        observation = OrderObservation.from_dict(json_value(observation))
        with self._transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (identifier(intent_id),)).fetchone()
            if (row is None or row["version"] != expected_version or OrderState(row["state"]) not in
                    {OrderState.SUBMITTING, OrderState.RECONCILING, OrderState.CANCEL_PENDING}):
                raise StateConflict("Observation requires the current in-progress control state.")
            record = self._record(row)
            intent = record.request.intent
            if (observation.account_id, observation.client_id, observation.symbol) != (
                    intent.account_id, intent.client_id, intent.symbol):
                raise ObservationError("Source identity differs from the persisted order.")
            if record.source_order_id is not None and record.source_order_id != observation.source_order_id:
                raise ObservationError("Stable source order identity changed.")
            previous = self._last_observation(db, intent_id)
            if previous is not None:
                if observation.observed_at < previous.observed_at:
                    raise ObservationError("A stale source observation cannot replace newer evidence.")
                if previous.state == OrderState.FILLED and observation.state != OrderState.FILLED:
                    raise ObservationError("A filled order cannot return to an open state.")
                if previous.state == OrderState.CANCELED and observation.state not in {OrderState.CANCELED, OrderState.FILLED}:
                    raise ObservationError("A canceled order cannot return to an open state.")
                if previous.state == OrderState.REJECTED and observation.state != OrderState.REJECTED:
                    raise ObservationError("Conflicting evidence after rejection requires manual review.")
                if observation.cumulative_filled_quantity < previous.cumulative_filled_quantity:
                    raise ObservationError("Cumulative fill quantity cannot decrease.")
            quantity = observation.cumulative_filled_quantity
            if quantity > intent.quantity_base:
                raise ObservationError("Cumulative quantity exceeds the persisted order quantity.")
            if observation.state in {OrderState.WORKING, OrderState.REJECTED} and quantity != 0:
                raise ObservationError("Working/rejected receipt contradicts nonzero fills.")
            if observation.state == OrderState.PARTIAL and not 0 < quantity < intent.quantity_base:
                raise ObservationError("Partial receipt must have a strict partial fill quantity.")
            if observation.state == OrderState.FILLED and quantity != intent.quantity_base:
                raise ObservationError("Filled receipt must cover the complete order quantity.")
            self._merge_fills(db, record, observation, at)
            db.execute("UPDATE intents SET state=?,version=version+1,source_order_id=?,"
                       "reported_filled_quantity=?,uncertainty_reason=NULL,last_observed_at=? WHERE intent_id=?",
                       (observation.state.value, observation.source_order_id,
                        decimal_text(observation.cumulative_filled_quantity),
                        json_value(observation.observed_at), intent_id))
            self._event(db, "order_observed", account_id=intent.account_id, run_id=intent.run_id,
                        trace_id=intent.trace_id, at=at, intent_id=intent_id,
                        payload={"observation": observation, "version": expected_version + 1})
            return self._record(db.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone())

    def receipt(self, intent_id: str) -> ExecutionReceipt:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM intents WHERE intent_id=?", (identifier(intent_id),)).fetchone()
            if row is None:
                raise LedgerError("Unknown intent identity.")
            record = self._record(row)
            intent = record.request.intent
            observation = self._last_observation(db, intent_id)
            fills = self._fills(db, intent_id)
            unresolved = record.state in {OrderState.PLANNED, OrderState.SUBMITTING, OrderState.UNKNOWN,
                                         OrderState.RECONCILING, OrderState.CANCEL_PENDING}
            filled = None if unresolved else record.reported_filled_quantity
            remaining = (exact_sum((intent.quantity_base, filled.copy_negate()))
                         if filled is not None else None)
            net, net_reason = None, "order_outcome_or_fill_evidence_unknown"
            if filled is not None and intent.side == Side.BUY:
                if any(fill.fee_amount is None for fill in fills):
                    net_reason = "one_or_more_fill_fees_unknown"
                else:
                    net = exact_sum([fill.quantity_base for fill in fills] + [
                        fill.fee_amount.copy_negate() for fill in fills if fill.fee_asset == intent.base_asset
                    ])
                    net_reason = None
            return ExecutionReceipt(intent.intent_id, intent.client_id, intent.account_id, intent.symbol,
                                    intent.mode, record.state, intent.run_id, intent.trace_id,
                                    record.request.fingerprint, record.request.references,
                                    intent.base_asset, intent.quote_asset, intent.quantity_base,
                                    filled, remaining, observation, fills, record.request.reservation,
                                    record.reservation_held, record.uncertainty_reason, net, net_reason)

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
            existing = self._matching(db, request)
            if existing is not None:
                return existing, False
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
