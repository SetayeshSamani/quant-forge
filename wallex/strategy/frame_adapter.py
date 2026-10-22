"""Read-only v0/v1 frame diagnostics; never trade authorization.

V0 is a passive attribute interface without a mandatory Market import. V1 uses
Market validation helpers lazily. Neither is an approved shared contract.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wallex.market.frame_v1 import MarketFrameV1

from .engine import Signal
from .indicators import (
    Candle, InvalidCandleData, TP1_HOURLY_WARMUP, Timeframe, validate_candles,
)
from .pullback import MIN_PULLBACK_HISTORY, PullbackAssessment, assess_pullback
from .stops import StopAssessment, assess_initial_stop
from .trend import TrendAssessment, assess_hourly_trend

SUPPORTED_SCHEMA = "market-frame-proposal-v0"
_SOURCE_REASONS = (
    "QUOTE_UNAVAILABLE", "DEPTH_UNAVAILABLE", "FRAME_TIME_SEMANTICS_PENDING",
    "SNAPSHOT_HASH_POLICY_PENDING", "SHARED_SCHEMA_PENDING",
)


class DiagnosticReason(str, Enum):
    FRAME_VALIDATED = "FRAME_VALIDATED"
    INVALID_FRAME = "INVALID_FRAME"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INVALID_AS_OF = "INVALID_AS_OF"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    INVALID_MARKET_EVIDENCE = "INVALID_MARKET_EVIDENCE"
    INVALID_HISTORY = "INVALID_HISTORY"
    CANDLE_IDENTITY_MISMATCH = "CANDLE_IDENTITY_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    STALE_HISTORY = "STALE_HISTORY"
    TECHNICAL_CONDITIONS_MET = "TECHNICAL_CONDITIONS_MET"
    TECHNICAL_CONDITIONS_NOT_MET = "TECHNICAL_CONDITIONS_NOT_MET"
    PE_UNAVAILABLE = "PE_UNAVAILABLE"
    PE_INVALID = "PE_INVALID"
    PE_PROVISIONAL = "PE_PROVISIONAL"
    STOP_INVALID = "STOP_INVALID"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    FRAME_QUALITY_INCOMPLETE = "FRAME_QUALITY_INCOMPLETE"
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    DEPTH_UNAVAILABLE = "DEPTH_UNAVAILABLE"
    FRAME_TIME_UNAVAILABLE = "FRAME_TIME_UNAVAILABLE"
    SNAPSHOT_HASH_UNAVAILABLE = "SNAPSHOT_HASH_UNAVAILABLE"
    SHARED_SCHEMA_UNAPPROVED = "SHARED_SCHEMA_UNAPPROVED"


_EXECUTION_BLOCKERS = (
    DiagnosticReason.DIAGNOSTIC_ONLY,
    DiagnosticReason.FRAME_QUALITY_INCOMPLETE,
    DiagnosticReason.QUOTE_UNAVAILABLE,
    DiagnosticReason.DEPTH_UNAVAILABLE,
    DiagnosticReason.FRAME_TIME_UNAVAILABLE,
    DiagnosticReason.SNAPSHOT_HASH_UNAVAILABLE,
    DiagnosticReason.SHARED_SCHEMA_UNAPPROVED,
)


@dataclass(frozen=True)
class HistoryDiagnostics:
    """Exact OHLC projection plus aligned base-volume and provenance claims.

    timeframe explicitly maps source name/seconds to Strategy's Enum.
    volumes_base[i] belongs to candles[i]. V1 has no dataset byte digest; its
    validated receipt/source/Quote/Depth evidence is in FrameMetadata.v1_evidence.
    """

    symbol: str
    timeframe: Timeframe
    dataset_sha256: str | None
    candles: tuple[Candle, ...]
    volumes_base: tuple[Decimal, ...]


@dataclass(frozen=True)
class FrameMetadata:
    schema_version: str
    symbol: str
    base_asset: str
    quote_asset: str
    as_of: int
    m15: HistoryDiagnostics
    h1: HistoryDiagnostics
    data_quality: str
    quality_reasons: tuple[str, ...]
    event_time: int | None = None
    received_time: int | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread_pct: Decimal | None = None
    depth_valid: bool = False
    snapshot_hash: str | None = None
    v1_evidence: "MarketFrameV1 | None" = None


@dataclass(frozen=True)
class FrameDiagnostics:
    """frame_valid means local validation only, never market/trading readiness.

    technical_eligible means Trend AND Pullback only, independent of optional Pe
    and stop validity. All executable fields are fixed to disabled values.
    """

    frame_valid: bool
    reasons: tuple[DiagnosticReason, ...]
    metadata: FrameMetadata | None = None
    trend: TrendAssessment | None = None
    pullback: PullbackAssessment | None = None
    stop: StopAssessment | None = None
    technical_eligible: bool = False
    rejected_at: str | None = None
    executable_action: Signal = field(default=Signal.NO_TRADE, init=False)
    executable_ready: bool = field(default=False, init=False)


class _Rejected(ValueError):
    def __init__(self, reason: DiagnosticReason, location: str):
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


def _text(value: object) -> bool:
    return (type(value) is str and bool(value)
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


def _code(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _history(
    source: object, *, symbol: str, timeframe: Timeframe, minimum: int,
    as_of: int, digest: object, location: str,
) -> HistoryDiagnostics:
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise _Rejected(DiagnosticReason.INVALID_PROVENANCE, location + ".dataset_sha256")
    if not isinstance(source, tuple):
        raise _Rejected(DiagnosticReason.INVALID_HISTORY, location)
    projected = []
    volumes = []
    for index, bar in enumerate(source):
        where = f"{location}[{index}]"
        bar_symbol = bar.symbol
        if type(bar_symbol) is not str or bar_symbol != symbol:
            raise _Rejected(DiagnosticReason.CANDLE_IDENTITY_MISMATCH, where + ".symbol")
        interval = bar.timeframe
        if (not isinstance(interval, Enum) or interval.name != timeframe.name
                or type(interval.value) is not int or interval.value != timeframe.value):
            raise _Rejected(DiagnosticReason.TIMEFRAME_MISMATCH, where + ".timeframe")
        volume = bar.volume
        if not isinstance(volume, Decimal) or not volume.is_finite() or volume < 0:
            raise _Rejected(DiagnosticReason.INVALID_HISTORY, where + ".volume")
        projected.append(Candle(
            timestamp=bar.timestamp, open=bar.open, high=bar.high, low=bar.low,
            close=bar.close, completed=bar.completed,
        ))
        volumes.append(volume)
    try:
        candles = validate_candles(projected, timeframe=timeframe, as_of=as_of)
    except InvalidCandleData as exc:
        raise _Rejected(DiagnosticReason.INVALID_HISTORY, location) from exc
    if len(candles) < minimum:
        raise _Rejected(DiagnosticReason.INSUFFICIENT_HISTORY, location)
    if candles[-1].timestamp + timeframe.value != as_of // timeframe.value * timeframe.value:
        raise _Rejected(DiagnosticReason.STALE_HISTORY, location)
    return HistoryDiagnostics(symbol, timeframe, digest, candles, tuple(volumes))


def _metadata_v1(frame: object) -> FrameMetadata:
    # Import only for an explicitly labelled v1 input; v0 stays independent.
    from wallex.market.frame_v1 import (
        FrameV1Error, MarketFrameV1, QualityV1, build_market_frame_v1,
        canonical_frame_json, verify_snapshot_hash,
    )
    if type(frame) is not MarketFrameV1:
        raise _Rejected(DiagnosticReason.INVALID_FRAME, "frame")
    if not verify_snapshot_hash(frame):
        raise _Rejected(DiagnosticReason.INVALID_PROVENANCE, "snapshot_hash")
    try:
        validated = build_market_frame_v1(
            frame.candles_15m, frame.candles_1h, symbol=frame.symbol,
            base_asset=frame.base_asset, quote_asset=frame.quote_asset,
            as_of=frame.as_of, quote=frame.quote, depth=frame.depth, policy=frame.policy,
        )
    except FrameV1Error as exc:
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, exc.field or "frame") from exc
    # A hash verifies bytes, not semantics. Rebuild derived fields with Market's
    # existing validator and compare canonical forms (so 0 cannot equal False).
    if canonical_frame_json(frame) != canonical_frame_json(validated):
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, "frame")
    if frame.quality is not QualityV1.COMPLETE or frame.quality_reasons:
        raise _Rejected(DiagnosticReason.FRAME_QUALITY_INCOMPLETE, "quality")

    def project(observations, timeframe):
        bars = tuple(item.candle for item in observations)
        return HistoryDiagnostics(
            validated.symbol, timeframe, None,
            tuple(Candle(b.timestamp, b.open, b.high, b.low, b.close, b.completed) for b in bars),
            tuple(b.volume for b in bars),
        )

    return FrameMetadata(
        validated.schema_version, validated.symbol, validated.base_asset,
        validated.quote_asset, validated.as_of,
        project(validated.candles_15m, Timeframe.M15),
        project(validated.candles_1h, Timeframe.H1), validated.quality.value,
        tuple(r.value for r in validated.quality_reasons),
        event_time=validated.event_time, received_time=validated.received_time,
        best_bid=validated.quote.best_bid, best_ask=validated.quote.best_ask,
        spread_pct=validated.spread_pct, depth_valid=validated.depth_valid,
        snapshot_hash=validated.snapshot_hash, v1_evidence=validated,
    )


def _metadata(frame: object) -> FrameMetadata:
    version = frame.schema_version
    if type(version) is str and version == "marketframe-v1":
        return _metadata_v1(frame)
    if type(version) is not str or version != SUPPORTED_SCHEMA:
        raise _Rejected(DiagnosticReason.UNSUPPORTED_SCHEMA, "schema_version")
    symbol, base, quote = frame.symbol, frame.base_asset, frame.quote_asset
    for name, value in (("symbol", symbol), ("base_asset", base), ("quote_asset", quote)):
        if not _text(value):
            raise _Rejected(DiagnosticReason.INVALID_IDENTITY, name)
    if base == quote:
        raise _Rejected(DiagnosticReason.INVALID_IDENTITY, "quote_asset")
    cutoff = frame.as_of
    if type(cutoff) is not int or cutoff < 0:
        raise _Rejected(DiagnosticReason.INVALID_AS_OF, "as_of")
    # v0 explicitly cannot carry approved Quote/Depth/time/hash evidence. Do not
    # accept forged readiness fields or upgrade this version's meaning silently.
    quality = _code(frame.data_quality)
    if type(quality) is not str or quality != "INCOMPLETE":
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, "data_quality")
    raw_reasons = frame.quality_reasons
    if not isinstance(raw_reasons, tuple):
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, "quality_reasons")
    quality_reasons = tuple(_code(value) for value in raw_reasons)
    if (not all(type(value) is str for value in quality_reasons)
            or len(quality_reasons) != len(_SOURCE_REASONS)
            or set(quality_reasons) != set(_SOURCE_REASONS)):
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, "quality_reasons")
    for name in ("event_time", "received_time", "best_bid", "best_ask", "spread_pct", "snapshot_hash"):
        if getattr(frame, name) is not None:
            raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, name)
    if frame.depth_valid is not False:
        raise _Rejected(DiagnosticReason.INVALID_MARKET_EVIDENCE, "depth_valid")
    m15 = _history(
        frame.candles_15m, symbol=symbol, timeframe=Timeframe.M15,
        minimum=MIN_PULLBACK_HISTORY, as_of=cutoff, digest=frame.dataset_sha256_15m,
        location="candles_15m",
    )
    h1 = _history(
        frame.candles_1h, symbol=symbol, timeframe=Timeframe.H1,
        minimum=TP1_HOURLY_WARMUP, as_of=cutoff, digest=frame.dataset_sha256_1h,
        location="candles_1h",
    )
    return FrameMetadata(version, symbol, base, quote, cutoff, m15, h1, quality, quality_reasons)


def assess_market_frame(frame: object = None, *, pe: Decimal | None = None) -> FrameDiagnostics:
    """Read a v0 attribute snapshot or validated typed v1 diagnostic snapshot.

    Both histories are fully validated before any assessment is invoked. Pe is
    the caller's provisional maximum permitted BUY price, never inferred. Invalid
    Pe skips stops but does not change the meaning of technical eligibility.
    A rejection exposes no metadata, candles or assessment results. Attribute
    errors/other ordinary exceptions at this external boundary fail closed without
    exposing arbitrary exception text. This is not a sandbox for active objects.
    """
    try:
        metadata = _metadata(frame)
        trend = assess_hourly_trend(metadata.h1.candles, as_of=metadata.as_of)
        pullback = assess_pullback(metadata.m15.candles, as_of=metadata.as_of)
        eligible = trend.eligible and pullback.eligible
        blockers = (_EXECUTION_BLOCKERS if metadata.schema_version == SUPPORTED_SCHEMA else
                    (DiagnosticReason.DIAGNOSTIC_ONLY, DiagnosticReason.SHARED_SCHEMA_UNAPPROVED))
        reasons = [DiagnosticReason.FRAME_VALIDATED, *blockers]
        reasons.append(DiagnosticReason.TECHNICAL_CONDITIONS_MET if eligible
                       else DiagnosticReason.TECHNICAL_CONDITIONS_NOT_MET)
        stop = None
        if pe is None:
            reasons.append(DiagnosticReason.PE_UNAVAILABLE)
        elif not isinstance(pe, Decimal) or not pe.is_finite() or pe <= 0:
            reasons.append(DiagnosticReason.PE_INVALID)
        else:
            reasons.append(DiagnosticReason.PE_PROVISIONAL)
            stop = assess_initial_stop(metadata.m15.candles, entry_price=pe, as_of=metadata.as_of)
            if not stop.valid:
                reasons.append(DiagnosticReason.STOP_INVALID)
        return FrameDiagnostics(True, tuple(reasons), metadata, trend, pullback, stop, eligible)
    except _Rejected as exc:
        return FrameDiagnostics(False, (exc.reason, DiagnosticReason.DIAGNOSTIC_ONLY),
                                rejected_at=exc.location)
    except Exception:
        # Do not retain partial evidence or exception messages from foreign objects.
        return FrameDiagnostics(False, (DiagnosticReason.INVALID_FRAME,
                                       DiagnosticReason.DIAGNOSTIC_ONLY), rejected_at="frame")
