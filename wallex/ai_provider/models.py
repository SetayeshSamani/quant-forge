"""Local v1 contracts. No order, execution, or authorization objects."""

from dataclasses import dataclass, field
import math
import re

VERSION = "1"
STATES = {"VERIFIED", "BLOCKED", "UNVERIFIED"}
REASONS = {
    "NO_TRADE": {"NO_SIGNAL", "TREND_UNFAVORABLE", "PULLBACK_ABSENT", "INSUFFICIENT_EVIDENCE"},
    "REVIEW": {"REVIEW_REQUIRED", "UNCERTAINTY"},
}


@dataclass(frozen=True)
class AdvisoryContext:
    """Caller-sanitized snapshot; VERIFIED is an attestation, not risk approval."""

    symbol: str
    last_price: float
    h1_trend: str
    m15_pullback: bool
    data_quality: str = "UNVERIFIED"
    risk: str = "UNVERIFIED"
    credit: str = "UNVERIFIED"
    version: str = VERSION

    def valid(self) -> bool:
        return (
            type(self.version) is str and self.version == VERSION
            and type(self.symbol) is str
            and re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,23}", self.symbol) is not None
            and type(self.last_price) in (int, float)
            and 0 < self.last_price <= 1e18 and math.isfinite(self.last_price)
            and type(self.h1_trend) is str
            and self.h1_trend in {"UP", "DOWN", "FLAT", "UNVERIFIED"}
            and type(self.m15_pullback) is bool
            and all(type(s) is str and s in STATES
                    for s in (self.data_quality, self.risk, self.credit))
        )


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class AdvisoryResult:
    decision: str
    reason_codes: tuple[str, ...]
    explanation: str
    status: str
    requested_model: str
    response_model: str | None = None
    usage: TokenUsage | None = None
    http_status: int | None = None
    response_sha256: str | None = None
    provider: str = field(default="BoxAPI", init=False)
    version: str = field(default=VERSION, init=False)
    executable: bool = field(default=False, init=False)
