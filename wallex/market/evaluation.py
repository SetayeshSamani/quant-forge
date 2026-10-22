"""Internal offline harness records, not CandidateSignal/TradeIntent/AuditEvent."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path

from .frames import FRAME_SCHEMA_VERSION, MarketFrameProposal
from .replay import ReplayPlan, ReplayStep


class Action(str, Enum):
    NO_TRADE = "NO_TRADE"
    BUY = "BUY"
    SELL = "SELL"


class EvidenceKind(str, Enum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    HISTORICAL_DATA = "historical_data"


@dataclass(frozen=True)
class ReplayDecision:
    """Offline evaluator output only; quantity is explicit base units, never sized here."""

    action: Action
    reason: str
    quantity_base: Decimal | None = None
    diagnostics_json: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, Action):
            raise ValueError("explicit Action enum required")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 1000:
            raise ValueError("a nonblank bounded reason is required")
        if self.diagnostics_json is not None:
            if (type(self.diagnostics_json) is not str or not self.diagnostics_json
                    or len(self.diagnostics_json) > 16384):
                raise ValueError("diagnostics must be a bounded nonempty JSON string")
            if type(json.loads(self.diagnostics_json)) is not dict:
                raise ValueError("diagnostics must encode an object")
        if self.action is Action.NO_TRADE:
            if self.quantity_base is not None:
                raise ValueError("NO_TRADE cannot carry an order quantity")
        elif (not isinstance(self.quantity_base, Decimal) or not self.quantity_base.is_finite()
              or self.quantity_base <= 0):
            raise ValueError("BUY/SELL require an explicit positive finite Decimal base quantity")


DecisionProvider = Callable[[MarketFrameProposal], ReplayDecision]


@dataclass(frozen=True)
class EvaluationRecord:
    as_of: int
    decision: ReplayDecision | None
    error_code: str | None
    error_message: str | None
    frame_quality: str | None
    quality_reasons: tuple[str, ...]


def evaluate_step(step: ReplayStep, provider: DecisionProvider) -> EvaluationRecord:
    if step.issue is not None:
        return EvaluationRecord(step.as_of, None, "DATA_" + step.issue.code,
                                step.issue.message, None, ())
    frame = step.frame
    quality = frame.data_quality.value
    reasons = tuple(r.value for r in frame.quality_reasons)
    try:
        decision = provider(frame)
    except Exception as exc:
        return EvaluationRecord(step.as_of, None, "PROVIDER_ERROR",
                                f"{type(exc).__name__}: {exc}", quality, reasons)
    try:
        if not isinstance(decision, ReplayDecision):
            raise ValueError("provider must return ReplayDecision")
        decision = ReplayDecision(decision.action, decision.reason, decision.quantity_base,
                                  decision.diagnostics_json)
    except Exception as exc:
        return EvaluationRecord(step.as_of, None, "INVALID_DECISION",
                                f"{type(exc).__name__}: {exc}", quality, reasons)
    return EvaluationRecord(step.as_of, decision, None, None, quality, reasons)


def json_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite Decimal cannot be exported")
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("report keys must be strings")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise ValueError(f"unsupported report value: {type(value).__name__}")


def canonical_json(value) -> str:
    """Deterministic harness/report serialization, NOT the pending frame hash policy."""
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def run_metadata(plan: ReplayPlan, *, mode: str, evidence_kind: EvidenceKind,
                 strategy_version: str, assumptions=None) -> str:
    if not isinstance(plan, ReplayPlan):
        raise ValueError("ReplayPlan required")
    if not isinstance(evidence_kind, EvidenceKind):
        raise ValueError("explicit EvidenceKind required; source authenticity is not inferred")
    if (not isinstance(strategy_version, str) or not strategy_version.strip()
            or len(strategy_version) > 200):
        raise ValueError("explicit bounded strategy_version required")
    return canonical_json({
        "schema_version": "offline-evaluation-v1", "mode": mode,
        "frame_schema_version": FRAME_SCHEMA_VERSION, "evidence_kind": evidence_kind,
        "strategy_version": strategy_version, "symbol": plan.m15.symbol,
        "base_asset": plan.base_asset, "quote_asset": plan.quote_asset,
        "start_as_of": plan.start_as_of, "end_as_of": plan.end_as_of,
        "source_m15": {"sha256": plan.m15.sha256, "timestamp_unit": plan.m15.timestamp_unit,
                       "as_of": plan.m15.as_of},
        "source_h1": {"sha256": plan.h1.sha256, "timestamp_unit": plan.h1.timestamp_unit,
                      "as_of": plan.h1.as_of},
        "assumptions": assumptions,
    })


@dataclass(frozen=True)
class RunEvent:
    event_id: str
    run_id: str
    sequence: int
    as_of: int
    mode: str
    evidence_kind: str
    kind: str
    payload_json: str

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)


class Journal:
    """Per-run in-memory journal; no execution hooks or teammate audit dependency."""

    def __init__(self, metadata_json: str) -> None:
        self.run_id = sha256(metadata_json.encode("ascii")).hexdigest()
        metadata = json.loads(metadata_json)
        self.mode = metadata["mode"]
        self.evidence_kind = metadata["evidence_kind"]
        self.events: list[RunEvent] = []

    def add(self, as_of: int, kind: str, payload) -> None:
        sequence = len(self.events)
        data = json_value(payload)
        if not isinstance(data, dict):
            data = {"data": data}
        payload_json = canonical_json(data | {"mode": self.mode, "evidence_kind": self.evidence_kind})
        event_id = sha256(canonical_json(
            [self.run_id, sequence, as_of, kind, payload_json],
        ).encode("ascii")).hexdigest()
        self.events.append(RunEvent(event_id, self.run_id, sequence, as_of, self.mode,
                                    self.evidence_kind, kind, payload_json))


def write_artifacts(directory, *, metadata_json: str, summary, events, tables=()) -> tuple[Path, ...]:
    """Explicit export into a NEW directory; existing files are never overwritten."""
    metadata = json.loads(metadata_json)
    identity = {"mode": metadata["mode"], "evidence_kind": metadata["evidence_kind"],
                "run_id": sha256(metadata_json.encode("ascii")).hexdigest()}
    contents = {
        "run.json": metadata_json + "\n",
        "summary.json": canonical_json(json_value(summary) | identity) + "\n",
        "events.jsonl": "".join(canonical_json(event) + "\n" for event in events),
    }
    for name, rows in tables:
        if name in contents or Path(name).name != name or not name.endswith(".jsonl"):
            raise ValueError("table names must be unique plain JSONL filenames")
        contents[name] = "".join(canonical_json(json_value(row) | identity) + "\n" for row in rows)
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=False)
    paths = []
    for name, content in contents.items():
        path = destination / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        paths.append(path)
    return tuple(paths)
