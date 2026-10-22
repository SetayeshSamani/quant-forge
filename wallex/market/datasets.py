"""Read a local JSONL byte snapshot into provisional, reproducible market data."""

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from os import PathLike
from pathlib import Path

from .normalization import (
    ErrorCode, MarketCandle, MarketDataError, Timeframe, TimestampUnit,
    normalize_candles,
)


class DatasetErrorCode(str, Enum):
    READ_ERROR = "READ_ERROR"
    INVALID_ENCODING = "INVALID_ENCODING"
    BLANK_LINE = "BLANK_LINE"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_JSON_NUMBER = "INVALID_JSON_NUMBER"
    NON_OBJECT_RECORD = "NON_OBJECT_RECORD"


class DatasetError(ValueError):
    """Dataset failure; normalization codes/index/field retain their meanings."""

    def __init__(
        self, code: DatasetErrorCode | ErrorCode, message: str, *,
        line_number: int | None = None, index: int | None = None,
        field: str | None = None,
    ) -> None:
        self.code = code
        self.line_number = line_number
        self.index = index
        self.field = field
        location = "dataset" if line_number is None else f"line {line_number}"
        if field is not None:
            location += f" ({field})"
        super().__init__(f"{code.value} at {location}: {message}")


@dataclass(frozen=True)
class LoadedDataset:
    """Immutable internal result, not MarketFrame or a complete experiment ID.

    sha256 identifies the original file bytes; candle timestamps are seconds.
    timestamp_unit records the input setting, not the output timestamp unit.
    Use load_dataset to construct validated results.
    """

    candles: tuple[MarketCandle, ...]
    sha256: str
    symbol: str
    timeframe: Timeframe
    timestamp_unit: TimestampUnit
    as_of: int

    @property
    def candle_count(self) -> int:
        return len(self.candles)

    @property
    def first_open_at(self) -> int | None:
        return self.candles[0].timestamp if self.candles else None

    @property
    def last_open_at(self) -> int | None:
        return self.candles[-1].timestamp if self.candles else None


class _JSONPolicyError(ValueError):
    def __init__(self, code: DatasetErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _reject_json_number(value: str) -> None:
    raise _JSONPolicyError(
        DatasetErrorCode.INVALID_JSON_NUMBER,
        "JSON floating-point and nonfinite tokens are forbidden; use decimal strings",
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _JSONPolicyError(DatasetErrorCode.DUPLICATE_KEY, "duplicate JSON object key")
        result[key] = value
    return result


def load_dataset(
    path: str | PathLike[str], *, symbol: str, timeframe: Timeframe,
    timestamp_unit: TimestampUnit, as_of: int,
) -> LoadedDataset:
    """Read once, parse strict UTF-8 JSONL, then normalize the complete batch.

    No input modification, inferred metadata, filtering, sorting or wall clock.
    LF/CRLF are supported; one terminal newline is optional. Empty bytes are
    valid empty history. Blank lines, BOMs and non-object JSON are not records.
    Failures are chained DatasetError exceptions; no partial result is returned.
    """
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise DatasetError(DatasetErrorCode.READ_ERROR, str(exc)) from exc

    if payload.startswith(b"\xef\xbb\xbf"):
        raise DatasetError(
            DatasetErrorCode.INVALID_ENCODING, "UTF-8 BOM is not permitted", line_number=1,
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        line_number = payload[:exc.start].count(b"\n") + 1
        raise DatasetError(
            DatasetErrorCode.INVALID_ENCODING, "file must be strict UTF-8",
            line_number=line_number,
        ) from exc

    # splitlines() would also split Unicode separators inside valid JSON strings.
    lines = text.split("\n") if text else []
    if lines and lines[-1] == "":
        lines.pop()
    records = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise DatasetError(
                DatasetErrorCode.BLANK_LINE, "blank lines are not records",
                line_number=line_number,
            )
        try:
            record = json.loads(
                line, parse_float=_reject_json_number, parse_constant=_reject_json_number,
                object_pairs_hook=_unique_object,
            )
        except _JSONPolicyError as exc:
            raise DatasetError(exc.code, str(exc), line_number=line_number) from exc
        except (ValueError, RecursionError) as exc:
            raise DatasetError(
                DatasetErrorCode.INVALID_JSON, "invalid JSON or parser limits exceeded",
                line_number=line_number,
            ) from exc
        if not isinstance(record, dict):
            raise DatasetError(
                DatasetErrorCode.NON_OBJECT_RECORD, "each line must contain one JSON object",
                line_number=line_number,
            )
        records.append(record)

    try:
        candles = normalize_candles(
            records, symbol=symbol, timeframe=timeframe,
            timestamp_unit=timestamp_unit, as_of=as_of,
        )
    except MarketDataError as exc:
        raise DatasetError(
            exc.code, str(exc), index=exc.index, field=exc.field,
            line_number=None if exc.index is None else exc.index + 1,
        ) from exc

    return LoadedDataset(
        candles=candles, sha256=sha256(payload).hexdigest(), symbol=symbol,
        timeframe=timeframe, timestamp_unit=timestamp_unit, as_of=as_of,
    )
