"""Experimental marketframe-v1 proposal; no approved schema or trading authority.

Time is explicit UTC Unix seconds. History needs per-candle receipt evidence;
closed time is never substituted for receipt. Unknown source event times prevent
COMPLETE quality. COMPLETE describes data checks only: executable_ready is fixed
False. No source, venue, grace period, account rule, or liquidity cutoff is chosen.

Malformed histories/configuration/timestamps and future evidence raise. Missing,
stale, foreign or invalid Quote/Depth produce diagnostic INCOMPLETE frames. Invalid
numeric/identity snapshots are excluded with reasons; past stale snapshots remain
visible as diagnostic evidence. Neither is silently upgraded into usable data.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from decimal import (
    Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, Underflow,
)
from enum import Enum
from fractions import Fraction
from hashlib import sha256
from hmac import compare_digest
import json
import re

from .frames import MIN_H1_HISTORY, MIN_M15_HISTORY
from .normalization import ErrorCode, MarketCandle, MarketDataError, Timeframe, TimestampUnit, normalize_candles

SCHEMA_VERSION_V1 = "marketframe-v1"
SPREAD_PRECISION = 34
MAX_DECIMAL_TEXT_LENGTH = 10000  # Serialization resource bound, not a trading limit.


class V1ErrorCode(str, Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_TIME_ORDER = "INVALID_TIME_ORDER"
    INVALID_HISTORY = "INVALID_HISTORY"
    HISTORY_IDENTITY_MISMATCH = "HISTORY_IDENTITY_MISMATCH"
    MISSING_RECEIVED_TIME = "MISSING_RECEIVED_TIME"
    DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
    FUTURE_EVENT_TIME = "FUTURE_EVENT_TIME"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_HISTORY = "STALE_HISTORY"
    INVALID_SNAPSHOT = "INVALID_SNAPSHOT"
    SERIALIZATION_ERROR = "SERIALIZATION_ERROR"


class FrameV1Error(ValueError):
    def __init__(self, code: V1ErrorCode | ErrorCode, message: str, *,
                 field: str | None = None, index: int | None = None) -> None:
        self.code = code
        self.field = field
        self.index = index
        location = field or "frame"
        if index is not None:
            location += f"[{index}]"
        super().__init__(f"{code.value} at {location}: {message}")


class QualityV1(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class QualityReasonV1(str, Enum):
    MISSING_QUOTE = "MISSING_QUOTE"
    MISSING_DEPTH = "MISSING_DEPTH"
    STALE_QUOTE = "STALE_QUOTE"
    STALE_DEPTH = "STALE_DEPTH"
    UNKNOWN_QUOTE_EVENT_TIME = "UNKNOWN_QUOTE_EVENT_TIME"
    UNKNOWN_DEPTH_EVENT_TIME = "UNKNOWN_DEPTH_EVENT_TIME"
    MISSING_QUOTE_RECEIVED_TIME = "MISSING_QUOTE_RECEIVED_TIME"
    MISSING_DEPTH_RECEIVED_TIME = "MISSING_DEPTH_RECEIVED_TIME"
    QUOTE_IDENTITY_MISMATCH = "QUOTE_IDENTITY_MISMATCH"
    DEPTH_IDENTITY_MISMATCH = "DEPTH_IDENTITY_MISMATCH"
    INVALID_QUOTE = "INVALID_QUOTE"
    INVALID_DEPTH = "INVALID_DEPTH"
    SPREAD_LIMIT_EXCEEDED = "SPREAD_LIMIT_EXCEEDED"


def _timestamp(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise FrameV1Error(V1ErrorCode.INVALID_TIMESTAMP, "nonnegative integer UTC seconds required", field=name)


def _identifier(value: object, name: str) -> None:
    if (type(value) is not str or not value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise FrameV1Error(V1ErrorCode.INVALID_CONFIG, "explicit identifier without whitespace/control characters required",
                           field=name)


def canonical_decimal(value: Decimal) -> str:
    """Finite plain decimal, no fractional trailing zeros, signed zero -> 0.

    Independent of ambient Decimal context; never calls normalize()/quantize().
    Expanded text is bounded to 10,000 characters to avoid unbounded allocation.
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        raise FrameV1Error(V1ErrorCode.SERIALIZATION_ERROR, "finite Decimal required")
    if value == 0:
        return "0"
    sign, digits, exponent = value.as_tuple()
    # Drop only redundant coefficient zeros before estimating expanded size.
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    point = end + exponent
    size = (point if exponent >= 0 else end + 1 if point > 0 else 2 - exponent) + sign
    if size > MAX_DECIMAL_TEXT_LENGTH:
        raise FrameV1Error(V1ErrorCode.SERIALIZATION_ERROR, "expanded Decimal exceeds text resource limit")
    coefficient = "".join(str(digit) for digit in digits[:end])
    if exponent >= 0:
        text = coefficient + "0" * exponent
    else:
        point = len(coefficient) + exponent
        text = (coefficient[:point] + "." + coefficient[point:] if point > 0
                else "0." + "0" * (-point) + coefficient)
    return ("-" if sign else "") + text


@dataclass(frozen=True)
class FramePolicyV1:
    """All thresholds are required caller inputs; no active trading defaults."""

    max_quote_age_seconds: int
    max_depth_age_seconds: int
    max_spread_pct: Decimal

    def __post_init__(self) -> None:
        _timestamp(self.max_quote_age_seconds, "max_quote_age_seconds")
        _timestamp(self.max_depth_age_seconds, "max_depth_age_seconds")
        canonical_decimal(self.max_spread_pct)
        if self.max_spread_pct < 0:
            raise FrameV1Error(V1ErrorCode.INVALID_CONFIG, "spread ceiling must be nonnegative percent",
                               field="max_spread_pct")


@dataclass(frozen=True)
class AvailableCandle:
    """Passive input wrapper; builder validates it. Receipt is explicit evidence."""

    candle: MarketCandle
    received_time: int | None
    source_id: str


@dataclass(frozen=True)
class QuoteSnapshotV1:
    symbol: str
    base_asset: str
    quote_asset: str
    source_id: str
    event_time: int | None
    received_time: int | None
    best_bid: Decimal
    best_ask: Decimal


@dataclass(frozen=True)
class BookLevelV1:
    price: Decimal
    quantity_base: Decimal


@dataclass(frozen=True)
class DepthSnapshotV1:
    """Explicit full consumed levels: strict descending bids/ascending asks.

    Both sides need positive prices/quantities and uncrossed top levels. No
    volume-participation or tradable-size threshold is inferred.
    """

    symbol: str
    base_asset: str
    quote_asset: str
    source_id: str
    event_time: int | None
    received_time: int | None
    bids: tuple[BookLevelV1, ...]
    asks: tuple[BookLevelV1, ...]


@dataclass(frozen=True)
class MarketFrameV1:
    """Internal immutable output; use builder for validated construction.

    snapshot_hash is an integrity checksum, not authenticity/authorization.
    It may be replaced for verification tests; executable_ready cannot be enabled.
    """

    symbol: str
    base_asset: str
    quote_asset: str
    as_of: int
    event_time: int
    received_time: int
    candles_15m: tuple[AvailableCandle, ...]
    candles_1h: tuple[AvailableCandle, ...]
    quote: QuoteSnapshotV1 | None
    depth: DepthSnapshotV1 | None
    policy: FramePolicyV1
    source_ids: tuple[str, ...]
    spread_pct: Decimal | None
    depth_valid: bool
    quality: QualityV1
    quality_reasons: tuple[QualityReasonV1, ...]
    snapshot_hash: str | None = None
    schema_version: str = field(default=SCHEMA_VERSION_V1, init=False)
    contract_status: str = field(default="DRAFT", init=False)
    executable_ready: bool = field(default=False, init=False)


def _validated_history(observations: Sequence[AvailableCandle], *, symbol: str,
                       timeframe: Timeframe, as_of: int) -> tuple[AvailableCandle, ...]:
    name = "candles_15m" if timeframe is Timeframe.M15 else "candles_1h"
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes, bytearray)):
        raise FrameV1Error(V1ErrorCode.INVALID_HISTORY, "sequence of AvailableCandle required", field=name)
    source = tuple(observations)
    records = []
    for index, observation in enumerate(source):
        if not isinstance(observation, AvailableCandle) or not isinstance(observation.candle, MarketCandle):
            raise FrameV1Error(V1ErrorCode.INVALID_HISTORY, "AvailableCandle wrapping MarketCandle required",
                               field=name, index=index)
        bar = observation.candle
        if bar.symbol != symbol or bar.timeframe is not timeframe:
            raise FrameV1Error(V1ErrorCode.HISTORY_IDENTITY_MISMATCH, "candle symbol/timeframe mismatch",
                               field=name, index=index)
        _identifier(observation.source_id, name + ".source_id")
        if observation.received_time is None:
            raise FrameV1Error(V1ErrorCode.MISSING_RECEIVED_TIME, "candle receipt cannot be inferred",
                               field=name, index=index)
        _timestamp(observation.received_time, name + ".received_time")
        if observation.received_time > as_of:
            raise FrameV1Error(V1ErrorCode.DATA_NOT_AVAILABLE, "candle was received after as_of",
                               field=name, index=index)
        for key in ("open", "high", "low", "close", "volume"):
            try:
                canonical_decimal(getattr(bar, key))
            except FrameV1Error as exc:
                raise FrameV1Error(exc.code, str(exc), field=name + "." + key, index=index) from exc
        records.append({key: getattr(bar, key) for key in
                        ("timestamp", "open", "high", "low", "close", "volume", "completed")})
    try:
        candles = normalize_candles(records, symbol=symbol, timeframe=timeframe,
                                    timestamp_unit=TimestampUnit.SECONDS, as_of=as_of)
    except MarketDataError as exc:
        raise FrameV1Error(exc.code, str(exc), field=name + "." + (exc.field or "record"), index=exc.index) from exc
    for index, (observation, bar) in enumerate(zip(source, candles)):
        if observation.received_time < bar.timestamp + timeframe.value:
            raise FrameV1Error(V1ErrorCode.INVALID_TIME_ORDER, "completed candle received before its close",
                               field=name, index=index)
    return tuple(AvailableCandle(bar, source[i].received_time, source[i].source_id)
                 for i, bar in enumerate(candles))


def _history(observations: Sequence[AvailableCandle], *, symbol: str,
             timeframe: Timeframe, as_of: int, minimum: int) -> tuple[AvailableCandle, ...]:
    candles = _validated_history(observations, symbol=symbol, timeframe=timeframe, as_of=as_of)
    name = "candles_15m" if timeframe is Timeframe.M15 else "candles_1h"
    if len(candles) < minimum:
        raise FrameV1Error(V1ErrorCode.INSUFFICIENT_HISTORY,
                           f"need {minimum} completed bars; received {len(candles)}", field=name)
    if candles[-1].candle.timestamp + timeframe.value != as_of // timeframe.value * timeframe.value:
        raise FrameV1Error(V1ErrorCode.STALE_HISTORY, "latest expected closed candle is unavailable", field=name)
    return candles


def _snapshot_times(snapshot, *, as_of: int, name: str) -> None:
    _identifier(snapshot.source_id, name + ".source_id")
    for key in ("event_time", "received_time"):
        value = getattr(snapshot, key)
        if value is not None:
            _timestamp(value, name + "." + key)
            if value > as_of:
                code = V1ErrorCode.FUTURE_EVENT_TIME if key == "event_time" else V1ErrorCode.DATA_NOT_AVAILABLE
                raise FrameV1Error(code, "future snapshot cannot enter a decision frame", field=name + "." + key)
    if (snapshot.event_time is not None and snapshot.received_time is not None
            and snapshot.event_time > snapshot.received_time):
        raise FrameV1Error(V1ErrorCode.INVALID_TIME_ORDER, "source event cannot follow its receipt", field=name)


def _identity(snapshot, symbol: str, base: str, quote: str) -> bool:
    values = (snapshot.symbol, snapshot.base_asset, snapshot.quote_asset)
    return all(type(value) is str for value in values) and values == (symbol, base, quote)


def _ages(snapshot, *, as_of: int, maximum: int, name: str) -> tuple[QualityReasonV1, ...]:
    reasons = []
    if snapshot.received_time is None:
        reasons.append(QualityReasonV1["MISSING_" + name + "_RECEIVED_TIME"])
    if snapshot.event_time is None:
        reasons.append(QualityReasonV1["UNKNOWN_" + name + "_EVENT_TIME"])
    if any(value is not None and as_of - value > maximum
           for value in (snapshot.event_time, snapshot.received_time)):
        reasons.append(QualityReasonV1["STALE_" + name])
    return tuple(reasons)


def _valid_quote(quote: QuoteSnapshotV1) -> bool:
    try:
        canonical_decimal(quote.best_bid)
        canonical_decimal(quote.best_ask)
    except FrameV1Error:
        return False
    return quote.best_bid > 0 and quote.best_ask >= quote.best_bid


def _valid_depth(depth: DepthSnapshotV1) -> bool:
    if not isinstance(depth.bids, tuple) or not isinstance(depth.asks, tuple) or not depth.bids or not depth.asks:
        return False
    for side, descending in ((depth.bids, True), (depth.asks, False)):
        previous = None
        for level in side:
            if not isinstance(level, BookLevelV1):
                return False
            try:
                canonical_decimal(level.price)
                canonical_decimal(level.quantity_base)
            except FrameV1Error:
                return False
            if level.price <= 0 or level.quantity_base <= 0:
                return False
            if previous is not None and (level.price >= previous if descending else level.price <= previous):
                return False
            previous = level.price
    return depth.asks[0].price >= depth.bids[0].price


def _spread(quote: QuoteSnapshotV1) -> tuple[Decimal, Fraction]:
    # Algebraically identical to 100*(ask-bid)/mid, rounded ONCE to 34 digits.
    bid, ask = Fraction(quote.best_bid), Fraction(quote.best_ask)
    exact = 200 * (ask - bid) / (ask + bid)
    context = Context(prec=SPREAD_PRECISION, rounding=ROUND_HALF_EVEN, Emin=-999999,
                      Emax=999999, flags=[], traps=[InvalidOperation, DivisionByZero, Overflow, Underflow])
    return context.divide(Decimal(exact.numerator), Decimal(exact.denominator)), exact


def build_market_frame_v1(
    candles_15m: Sequence[AvailableCandle], candles_1h: Sequence[AvailableCandle], *,
    symbol: str, base_asset: str, quote_asset: str, as_of: int,
    quote: QuoteSnapshotV1 | None, depth: DepthSnapshotV1 | None, policy: FramePolicyV1,
) -> MarketFrameV1:
    """Build a draft snapshot. COMPLETE only means this version's data checks pass.

    Quotes/books are not cross-synchronized or checked against a venue registry.
    Limits, source clocks and input authenticity remain caller claims requiring
    team approval. Receipt/event age limits are inclusive; spread admission uses
    the exact ratio, not a rounded value that could hide a threshold breach.
    """
    _timestamp(as_of, "as_of")
    for key, value in (("symbol", symbol), ("base_asset", base_asset), ("quote_asset", quote_asset)):
        _identifier(value, key)
    if base_asset == quote_asset:
        raise FrameV1Error(V1ErrorCode.INVALID_CONFIG, "base/quote must differ", field="quote_asset")
    if not isinstance(policy, FramePolicyV1):
        raise FrameV1Error(V1ErrorCode.INVALID_CONFIG, "explicit FramePolicyV1 required", field="policy")
    policy = FramePolicyV1(policy.max_quote_age_seconds, policy.max_depth_age_seconds, policy.max_spread_pct)
    m15 = _history(candles_15m, symbol=symbol, timeframe=Timeframe.M15, as_of=as_of, minimum=MIN_M15_HISTORY)
    h1 = _history(candles_1h, symbol=symbol, timeframe=Timeframe.H1, as_of=as_of, minimum=MIN_H1_HISTORY)
    reasons = []
    spread, depth_valid = None, False
    if quote is None:
        reasons.append(QualityReasonV1.MISSING_QUOTE)
    else:
        if not isinstance(quote, QuoteSnapshotV1):
            raise FrameV1Error(V1ErrorCode.INVALID_SNAPSHOT, "QuoteSnapshotV1 required", field="quote")
        _snapshot_times(quote, as_of=as_of, name="quote")
        if not _identity(quote, symbol, base_asset, quote_asset):
            reasons.append(QualityReasonV1.QUOTE_IDENTITY_MISMATCH)
            quote = None
        elif not _valid_quote(quote):
            reasons.append(QualityReasonV1.INVALID_QUOTE)
            quote = None
        elif quote.received_time is None:
            reasons.extend(_ages(quote, as_of=as_of, maximum=policy.max_quote_age_seconds, name="QUOTE"))
            quote = None  # Availability is unproven: do not expose its prices.
        else:
            quote = replace(quote)
            quote_issues = _ages(quote, as_of=as_of, maximum=policy.max_quote_age_seconds, name="QUOTE")
            reasons.extend(quote_issues)
            if not quote_issues:
                spread, exact = _spread(quote)
                if exact > Fraction(policy.max_spread_pct):
                    reasons.append(QualityReasonV1.SPREAD_LIMIT_EXCEEDED)
    if depth is None:
        reasons.append(QualityReasonV1.MISSING_DEPTH)
    else:
        if not isinstance(depth, DepthSnapshotV1):
            raise FrameV1Error(V1ErrorCode.INVALID_SNAPSHOT, "DepthSnapshotV1 required", field="depth")
        _snapshot_times(depth, as_of=as_of, name="depth")
        if not _identity(depth, symbol, base_asset, quote_asset):
            reasons.append(QualityReasonV1.DEPTH_IDENTITY_MISMATCH)
            depth = None
        elif not _valid_depth(depth):
            reasons.append(QualityReasonV1.INVALID_DEPTH)
            depth = None
        elif depth.received_time is None:
            reasons.extend(_ages(depth, as_of=as_of, maximum=policy.max_depth_age_seconds, name="DEPTH"))
            depth = None
        else:
            depth = replace(depth, bids=tuple(replace(level) for level in depth.bids),
                            asks=tuple(replace(level) for level in depth.asks))
            depth_issues = _ages(depth, as_of=as_of, maximum=policy.max_depth_age_seconds, name="DEPTH")
            reasons.extend(depth_issues)
            depth_valid = not depth_issues
    used = (*m15, *h1, *((quote,) if quote else ()), *((depth,) if depth else ()))
    frame = MarketFrameV1(
        symbol, base_asset, quote_asset, as_of, m15[-1].candle.timestamp + Timeframe.M15.value,
        max(item.received_time for item in used), m15, h1, quote, depth, policy,
        tuple(sorted({item.source_id for item in used})), spread, depth_valid,
        QualityV1.INCOMPLETE if reasons else QualityV1.COMPLETE, tuple(reasons),
    )
    return replace(frame, snapshot_hash=compute_snapshot_hash(frame))


def _json_value(value):
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, Timeframe):
        return value.name
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (AvailableCandle, MarketCandle, FramePolicyV1, QuoteSnapshotV1,
                          DepthSnapshotV1, BookLevelV1, MarketFrameV1)):
        result = {}
        for f in fields(value):
            if isinstance(value, MarketFrameV1) and f.name == "snapshot_hash":
                continue
            item = getattr(value, f.name)
            if f.name in ("as_of", "timestamp", "event_time", "received_time") and item is not None:
                _timestamp(item, f.name)
            if f.name == "completed" and type(item) is not bool:
                raise FrameV1Error(V1ErrorCode.SERIALIZATION_ERROR, "boolean completed required")
            result[f.name] = _json_value(item)
        return result
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise FrameV1Error(V1ErrorCode.SERIALIZATION_ERROR, f"unsupported type {type(value).__name__}")


def canonical_frame_json(frame: MarketFrameV1) -> str:
    """Fixed draft JSON shape, always excluding snapshot_hash. Unicode is not normalized.

    Candles are nested wrapper records, levels are ordered price/quantity records,
    timeframes are M15/H1 strings, sources are sorted, quality reasons retain the
    deterministic quote-then-depth check order. No computed strategy features are
    included because this builder does not consume any.
    """
    if not isinstance(frame, MarketFrameV1):
        raise FrameV1Error(V1ErrorCode.SERIALIZATION_ERROR, "MarketFrameV1 required")
    return json.dumps(_json_value(frame), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def compute_snapshot_hash(frame: MarketFrameV1) -> str:
    return sha256(canonical_frame_json(frame).encode("utf-8")).hexdigest()


def verify_snapshot_hash(frame: MarketFrameV1) -> bool:
    """Integrity only: this does not validate semantics or authenticate the producer."""
    if not isinstance(frame, MarketFrameV1):
        return False
    digest = frame.snapshot_hash
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    try:
        expected = compute_snapshot_hash(frame)
    except (FrameV1Error, ValueError):
        return False
    return compare_digest(digest, expected)
