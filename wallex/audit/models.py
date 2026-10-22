"""Append-only API view of committed local audit events."""

from dataclasses import dataclass
from datetime import datetime
import json

AUDIT_SCHEMA_VERSION = "audit-ledger-v1"


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    schema_version: str
    kind: str
    intent_id: str | None
    account_id: str
    run_id: str
    trace_id: str
    occurred_at: datetime
    payload_json: str

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)
