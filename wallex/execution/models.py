"""Provisional consumers, not approved shared schemas or trading authorization.

No quantities, reservations, account limits, symbols or client IDs are generated.
Amounts are Decimal; quantity is base units and price is quote units per base.
"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal, Inexact, localcontext
from enum import Enum
import hashlib
import json

from wallex.risk import RiskDecision, RiskReason

SCHEMA_VERSION = "execution-consumer-v1"


class ContractError(ValueError):
    pass


class Mode(str, Enum):
    REPLAY = "replay"
    SHADOW = "shadow"
    LIVE = "live"


class OrderState(str, Enum):
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    CANCEL_PENDING = "CANCEL_PENDING"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def identifier(value: object) -> str:
    if (not isinstance(value, str) or not value or value.strip() != value
            or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ContractError("A bounded exact nonblank identifier is required.")
    return value


def amount(value: object, *, zero: bool = False) -> Decimal:
    if (not isinstance(value, Decimal) or not value.is_finite()
            or value < 0 or (not zero and value == 0)
            or len(value.as_tuple().digits) > 200 or abs(value.adjusted()) > 200):
        raise ContractError("A finite representable Decimal amount with the required sign is required.")
    return value


def utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("An aware timestamp is required.")
    return value.astimezone(timezone.utc)


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def exact_sum(values) -> Decimal:
    # Storage bounds above keep this exact without relying on the caller's context.
    with localcontext() as context:
        context.prec = 1024
        context.traps[Inexact] = True
        return sum(values, Decimal(0))


def exact_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 1024
        context.traps[Inexact] = True
        return left * right


def json_value(value):
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


def canonical_json(value) -> str:
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    client_id: str
    account_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    side: Side
    order_type: str
    quantity_base: Decimal
    price_quote_per_base: Decimal
    created_at: datetime
    expires_at: datetime
    mode: Mode
    run_id: str
    trace_id: str
    purpose: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for name in ("intent_id", "client_id", "account_id", "symbol", "base_asset", "quote_asset",
                     "run_id", "trace_id"):
            identifier(getattr(self, name))
        if self.base_asset == self.quote_asset:
            raise ContractError("Base and quote assets must be distinct explicit units.")
        if not isinstance(self.side, Side) or not isinstance(self.mode, Mode):
            raise ContractError("Explicit Side and Mode enum values are required.")
        if self.order_type != "LIMIT" or self.purpose not in ("entry", "risk_reducing_exit"):
            raise ContractError("Only an explicit LIMIT intent and recognized purpose are modeled.")
        amount(self.quantity_base)
        amount(self.price_quote_per_base)
        object.__setattr__(self, "created_at", utc(self.created_at))
        object.__setattr__(self, "expires_at", utc(self.expires_at))
        if self.expires_at <= self.created_at or self.schema_version != SCHEMA_VERSION:
            raise ContractError("A supported schema and explicit future intent deadline are required.")


@dataclass(frozen=True)
class DecisionReferences:
    risk_decision_id: str
    risk_policy_version: str
    strategy_version: str
    account_snapshot_id: str
    agent_decision_id: str | None
    agent_unknown_reason: str | None

    def __post_init__(self):
        for name in ("risk_decision_id", "risk_policy_version", "strategy_version", "account_snapshot_id"):
            identifier(getattr(self, name))
        if self.agent_decision_id is None:
            identifier(self.agent_unknown_reason)
        else:
            identifier(self.agent_decision_id)
            if self.agent_unknown_reason is not None:
                raise ContractError("Known Agent references cannot have an unknown reason.")


@dataclass(frozen=True)
class Reservation:
    asset: str
    amount: Decimal
    capacity_limit: Decimal

    def __post_init__(self):
        identifier(self.asset)
        amount(self.amount)
        amount(self.capacity_limit)
        if self.amount > self.capacity_limit:
            raise ContractError("Explicit reservation exceeds its supplied capacity limit.")


@dataclass(frozen=True)
class ExecutionRequest:
    intent: TradeIntent
    risk_decision: RiskDecision
    references: DecisionReferences
    reservation: Reservation
    evidence_kind: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if (not isinstance(self.intent, TradeIntent) or not isinstance(self.references, DecisionReferences)
                or not isinstance(self.reservation, Reservation)
                or not isinstance(self.risk_decision, RiskDecision)
                or type(self.risk_decision.allowed) is not bool
                or not isinstance(self.risk_decision.reason, RiskReason)
                or self.schema_version != SCHEMA_VERSION):
            raise ContractError("Typed provisional request components and supported version are required.")
        if self.evidence_kind not in ("synthetic_fixture", "shadow_decision", "live_verified"):
            raise ContractError("Only explicit offline evidence is supported.")
        if self.intent.mode == Mode.REPLAY and self.evidence_kind != "synthetic_fixture":
            raise ContractError("Replay requires explicitly synthetic evidence.")
        if self.evidence_kind == "live_verified" and self.intent.mode != Mode.LIVE:
            raise ContractError("Live evidence requires explicit live mode.")
        expected = self.intent.quote_asset if self.intent.side == Side.BUY else self.intent.base_asset
        minimum = (exact_product(self.intent.quantity_base, self.intent.price_quote_per_base)
                   if self.intent.side == Side.BUY else self.intent.quantity_base)
        if self.reservation.asset != expected or self.reservation.amount < minimum:
            raise ContractError("Reservation must use the correct asset and cover the explicit order.")

    def to_dict(self) -> dict:
        return json_value(self)

    @property
    def fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self).encode("ascii")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRequest":
        # Only stored canonical data, not a public transport decoder or authorization boundary.
        intent = dict(data["intent"])
        intent.update(side=Side(intent["side"]), mode=Mode(intent["mode"]),
                      quantity_base=Decimal(intent["quantity_base"]),
                      price_quote_per_base=Decimal(intent["price_quote_per_base"]),
                      created_at=datetime.fromisoformat(intent["created_at"]),
                      expires_at=datetime.fromisoformat(intent["expires_at"]))
        risk = data["risk_decision"]
        reservation = data["reservation"]
        return cls(TradeIntent(**intent), RiskDecision(risk["allowed"], RiskReason(risk["reason"])),
                   DecisionReferences(**data["references"]),
                   Reservation(reservation["asset"], Decimal(reservation["amount"]),
                               Decimal(reservation["capacity_limit"])),
                   data["evidence_kind"], data["schema_version"])


@dataclass(frozen=True)
class Fill:
    source: str
    fill_id: str
    source_order_id: str
    account_id: str
    client_id: str
    symbol: str
    base_asset: str
    quote_asset: str
    quantity_base: Decimal
    price_quote_per_base: Decimal
    fee_amount: Decimal | None
    fee_asset: str | None
    fee_unknown_reason: str | None
    source_time: datetime | None
    source_time_unknown_reason: str | None
    received_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for name in ("source", "fill_id", "source_order_id", "account_id", "client_id", "symbol",
                     "base_asset", "quote_asset"):
            identifier(getattr(self, name))
        amount(self.quantity_base)
        amount(self.price_quote_per_base)
        if self.fee_amount is None:
            if self.fee_asset is not None:
                raise ContractError("Unknown fees cannot claim a known fee asset.")
            identifier(self.fee_unknown_reason)
        else:
            amount(self.fee_amount, zero=True)
            identifier(self.fee_asset)
            if self.fee_unknown_reason is not None:
                raise ContractError("Known fees cannot have an unknown reason.")
        if self.source_time is None:
            identifier(self.source_time_unknown_reason)
        else:
            object.__setattr__(self, "source_time", utc(self.source_time))
            if self.source_time_unknown_reason is not None:
                raise ContractError("Known source times cannot have an unknown reason.")
        object.__setattr__(self, "received_at", utc(self.received_at))
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("Unsupported Fill schema version.")

    @classmethod
    def from_dict(cls, data: dict) -> "Fill":
        data = dict(data)
        for name in ("quantity_base", "price_quote_per_base", "fee_amount"):
            if data[name] is not None:
                data[name] = Decimal(data[name])
        for name in ("source_time", "received_at"):
            if data[name] is not None:
                data[name] = datetime.fromisoformat(data[name])
        return cls(**data)


@dataclass(frozen=True)
class AccountSnapshot:
    """Separate account contract; order reconciliation never constructs balances."""

    snapshot_id: str
    account_id: str
    observed_at: datetime
    balances: dict | None
    unknown_reason: str | None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        identifier(self.snapshot_id)
        identifier(self.account_id)
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        # This stage has no real account collector or reconciled balance schema.
        if self.balances is not None:
            raise ContractError("Known account balances require the future agreed account contract.")
        identifier(self.unknown_reason)
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("Unsupported account schema version.")


@dataclass(frozen=True)
class OrderObservation:
    """Explicit synthetic source evidence, not an account snapshot."""

    account_id: str
    client_id: str
    symbol: str
    source: str
    source_order_id: str | None
    state: OrderState
    cumulative_filled_quantity: Decimal
    fills: tuple[Fill, ...]
    observed_at: datetime
    source_time: datetime | None
    source_time_unknown_reason: str | None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for name in ("account_id", "client_id", "symbol", "source"):
            identifier(getattr(self, name))
        if self.source_order_id is None:
            if self.state != OrderState.REJECTED:
                raise ContractError("Accepted order evidence requires a stable source order ID.")
        else:
            identifier(self.source_order_id)
        if not isinstance(self.state, OrderState) or self.state not in {
            OrderState.WORKING, OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED
        }:
            raise ContractError("Only observed source receipt states are accepted.")
        amount(self.cumulative_filled_quantity, zero=True)
        if not isinstance(self.fills, tuple) or not all(isinstance(fill, Fill) for fill in self.fills):
            raise ContractError("Fill evidence must be a tuple of actual Fill consumers.")
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        if self.source_time is None:
            identifier(self.source_time_unknown_reason)
        else:
            object.__setattr__(self, "source_time", utc(self.source_time))
            if self.source_time_unknown_reason is not None:
                raise ContractError("Known source time cannot have an unknown reason.")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("Unsupported observation schema.")

    @classmethod
    def from_dict(cls, data: dict) -> "OrderObservation":
        data = dict(data)
        data["state"] = OrderState(data["state"])
        data["cumulative_filled_quantity"] = Decimal(data["cumulative_filled_quantity"])
        data["fills"] = tuple(Fill.from_dict(fill) for fill in data["fills"])
        for name in ("observed_at", "source_time"):
            if data[name] is not None:
                data[name] = datetime.fromisoformat(data[name])
        return cls(**data)


@dataclass(frozen=True)
class ExecutionReceipt:
    intent_id: str
    client_id: str
    account_id: str
    symbol: str
    mode: Mode
    state: OrderState
    run_id: str
    trace_id: str
    request_fingerprint: str
    references: DecisionReferences
    base_asset: str
    quote_asset: str
    ordered_quantity_base: Decimal
    cumulative_filled_quantity: Decimal | None
    remaining_quantity_base: Decimal | None
    last_observation: OrderObservation | None
    fills: tuple[Fill, ...]
    reservation: Reservation
    reservation_held: bool
    uncertainty_reason: str | None
    known_net_acquired_base: Decimal | None
    net_quantity_unknown_reason: str | None
    sellable_quantity_base: None = None
    sellable_unknown_reason: str = "account_free_balance_not_reconciled"
    reconciliation_scope: str = "synthetic_order_only"
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return json_value(self)
