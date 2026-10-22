"""Causal M15 decision windows over local datasets; no data fetches or orders."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from collections.abc import Iterator

from .datasets import LoadedDataset
from .frames import MarketFrameError, MarketFrameProposal, build_market_frame


@dataclass(frozen=True)
class ReplayIssue:
    code: str
    message: str
    dataset: str | None
    index: int | None
    field: str | None


@dataclass(frozen=True)
class ReplayStep:
    as_of: int
    frame: MarketFrameProposal | None
    issue: ReplayIssue | None


@dataclass(frozen=True)
class ReplayPlan:
    """Validated finite offline window. Source hashes identify complete files.

    Source datasets must form a valid final history frame at their shared load
    cutoff. The requested window may begin during warm-up, but only within M15
    coverage. Intermediate shortages become logged issues, never fabricated bars.
    """

    m15: LoadedDataset
    h1: LoadedDataset
    base_asset: str
    quote_asset: str
    start_as_of: int
    end_as_of: int

    def __post_init__(self) -> None:
        for name in ("start_as_of", "end_as_of"):
            value = getattr(self, name)
            if type(value) is not int or value < 0 or value % 900:
                raise ValueError(f"{name} must be a nonnegative UTC second on the M15 grid")
        if self.start_as_of > self.end_as_of:
            raise ValueError("start_as_of must not exceed end_as_of")
        if not isinstance(self.m15, LoadedDataset) or not isinstance(self.h1, LoadedDataset):
            raise ValueError("explicit LoadedDataset inputs are required")
        final = build_market_frame(self.m15, self.h1, base_asset=self.base_asset,
                                   quote_asset=self.quote_asset, as_of=self.m15.as_of)
        # Keep only freshly validated immutable records, not caller containers.
        object.__setattr__(self, "m15", replace(self.m15, candles=final.candles_15m))
        object.__setattr__(self, "h1", replace(self.h1, candles=final.candles_1h))
        if self.start_as_of < self.m15.candles[0].timestamp + 900:
            raise ValueError("decision window starts before M15 closed-bar coverage")
        if self.end_as_of > self.m15.candles[-1].timestamp + 900:
            raise ValueError("decision window ends after M15 closed-bar coverage")

    def steps(self) -> Iterator[ReplayStep]:
        """One step per M15 close, including failures; no future bar reaches a callback."""
        closes15 = tuple(c.timestamp + 900 for c in self.m15.candles)
        closes1h = tuple(c.timestamp + 3600 for c in self.h1.candles)
        for cutoff in range(self.start_as_of, self.end_as_of + 1, 900):
            m15 = replace(self.m15, candles=self.m15.candles[:bisect_right(closes15, cutoff)],
                          as_of=cutoff)
            h1 = replace(self.h1, candles=self.h1.candles[:bisect_right(closes1h, cutoff)],
                         as_of=cutoff)
            try:
                frame = build_market_frame(m15, h1, base_asset=self.base_asset,
                                           quote_asset=self.quote_asset, as_of=cutoff)
            except MarketFrameError as exc:
                yield ReplayStep(cutoff, None, ReplayIssue(
                    exc.code.value, str(exc), exc.dataset, exc.index, exc.field,
                ))
            else:
                yield ReplayStep(cutoff, frame, None)
