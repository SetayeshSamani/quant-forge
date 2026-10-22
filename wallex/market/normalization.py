"""Pure normalization of a provisional OHLCV format, not an exchange adapter."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, InvalidOperation
from enum import Enum
import re


class Timeframe(Enum):
    M1 = 60
    M15 = 900
    H1 = 3600


class TimestampUnit(str, Enum):
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"


class ErrorCode(str, Enum):
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_RECORDS = "INVALID_RECORDS"
    INVALID_RECORD = "INVALID_RECORD"
    MISSING_FIELD = "MISSING_FIELD"
    UNEXPECTED_FIELD = "UNEXPECTED_FIELD"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    SUBSECOND_TIMESTAMP = "SUBSECOND_TIMESTAMP"
    UNALIGNED_TIMESTAMP = "UNALIGNED_TIMESTAMP"
    INVALID_NUMBER = "INVALID_NUMBER"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INVALID_OHLC = "INVALID_OHLC"
    INVALID_COMPLETION = "INVALID_COMPLETION"
    INCOMPLETE_CANDLE = "INCOMPLETE_CANDLE"
    FUTURE_CANDLE = "FUTURE_CANDLE"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    MISSING_CANDLE = "MISSING_CANDLE"


class MarketDataError(ValueError):
    """First failure, with a stable code, zero-based record index, and field."""

    def __init__(
        self, code: ErrorCode, message: str, *,
        index: int | None = None, field: str | None = None,
    ) -> None:
        self.code = code
        self.index = index
        self.field = field
        location = "input" if index is None else f"records[{index}]"
        if field is not None:
            location += f".{field}"
        super().__init__(f"{code.value} at {location}: {message}")


@dataclass(frozen=True)
class MarketCandle:
    """Internal immutable output container; construct via normalize_candles.

    Timestamp is the UTC Unix-second OPEN time. Volume is base-asset volume.
    These records are provisional and are not the shared MarketFrame contract.
    """

    symbol: str
    timeframe: Timeframe
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    completed: bool


_FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "completed")
_PRICES = ("open", "high", "low", "close")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _number(value: object, *, index: int, field: str) -> Decimal:
    # Decimal construction is exact: no float conversion, rounding or quantizing.
    if not (
        isinstance(value, Decimal)
        or type(value) is int
        or (type(value) is str and _NUMBER.fullmatch(value) is not None)
    ):
        raise MarketDataError(
            ErrorCode.INVALID_NUMBER, "expected Decimal, integer, or ASCII decimal text",
            index=index, field=field,
        )
    try:
        result = (value if isinstance(value, Decimal)
                  else Decimal(value, context=Context(traps=[InvalidOperation])))
    except (DecimalException, ValueError, OverflowError) as exc:
        raise MarketDataError(
            ErrorCode.INVALID_NUMBER, "number cannot be represented as Decimal",
            index=index, field=field,
        ) from exc
    if not result.is_finite():
        raise MarketDataError(
            ErrorCode.INVALID_NUMBER, "number must be finite", index=index, field=field,
        )
    return result


def normalize_candles(
    records: Sequence[Mapping[str, object]], *, symbol: str,
    timeframe: Timeframe, timestamp_unit: TimestampUnit, as_of: int,
) -> tuple[MarketCandle, ...]:
    """Normalize one explicitly identified instrument/timeframe, or raise.

    Input keys are exactly timestamp/open/high/low/close/volume/completed.
    No row is sorted, dropped, deduplicated, filled, rounded, or inferred.
    as_of is always UTC Unix seconds, regardless of timestamp_unit.
    Empty history is allowed; indicator warm-up and freshness are separate.
    """
    if not isinstance(symbol, str) or not symbol or any(c.isspace() for c in symbol):
        raise MarketDataError(
            ErrorCode.INVALID_CONFIG, "symbol must be nonempty text without whitespace",
            field="symbol",
        )
    if not isinstance(timeframe, Timeframe):
        raise MarketDataError(
            ErrorCode.INVALID_CONFIG, "expected a supported Timeframe",
            field="timeframe",
        )
    if not isinstance(timestamp_unit, TimestampUnit):
        raise MarketDataError(
            ErrorCode.INVALID_CONFIG, "timestamp_unit must be an explicit TimestampUnit",
            field="timestamp_unit",
        )
    if type(as_of) is not int or as_of < 0:
        raise MarketDataError(
            ErrorCode.INVALID_CONFIG, "as_of must be nonnegative integer UTC Unix seconds",
            field="as_of",
        )
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise MarketDataError(ErrorCode.INVALID_RECORDS, "records must be a sequence")

    result = []
    previous = None
    seen = set()
    for index, source in enumerate(tuple(records)):
        if not isinstance(source, Mapping):
            raise MarketDataError(
                ErrorCode.INVALID_RECORD, "record must be a mapping", index=index,
            )
        record = dict(source)
        for field in _FIELDS:
            if field not in record:
                raise MarketDataError(
                    ErrorCode.MISSING_FIELD, "required field is missing",
                    index=index, field=field,
                )
        if any(field not in _FIELDS for field in record):
            raise MarketDataError(
                ErrorCode.UNEXPECTED_FIELD, "only documented OHLCV fields are accepted",
                index=index,
            )

        timestamp = record["timestamp"]
        if type(timestamp) is not int or timestamp < 0:
            raise MarketDataError(
                ErrorCode.INVALID_TIMESTAMP, "expected a nonnegative integer open time",
                index=index, field="timestamp",
            )
        if timestamp_unit is TimestampUnit.MILLISECONDS:
            if timestamp % 1000:
                raise MarketDataError(
                    ErrorCode.SUBSECOND_TIMESTAMP, "milliseconds must represent whole seconds",
                    index=index, field="timestamp",
                )
            timestamp //= 1000
        if timestamp % timeframe.value:
            raise MarketDataError(
                ErrorCode.UNALIGNED_TIMESTAMP, "open time must align to the UTC timeframe grid",
                index=index, field="timestamp",
            )

        numbers = {field: _number(record[field], index=index, field=field)
                   for field in (*_PRICES, "volume")}
        for field in _PRICES:
            if numbers[field] <= 0:
                raise MarketDataError(
                    ErrorCode.NON_POSITIVE_PRICE, "price must be greater than zero",
                    index=index, field=field,
                )
        if numbers["volume"] < 0:
            raise MarketDataError(
                ErrorCode.NEGATIVE_VOLUME, "base-asset volume must be nonnegative",
                index=index, field="volume",
            )
        if not (numbers["low"] <= numbers["open"] <= numbers["high"]
                and numbers["low"] <= numbers["close"] <= numbers["high"]):
            raise MarketDataError(
                ErrorCode.INVALID_OHLC, "open and close must lie between low and high",
                index=index, field="ohlc",
            )
        if type(record["completed"]) is not bool:
            raise MarketDataError(
                ErrorCode.INVALID_COMPLETION, "completed must be a boolean",
                index=index, field="completed",
            )
        if not record["completed"]:
            raise MarketDataError(
                ErrorCode.INCOMPLETE_CANDLE, "open candles are not consumable",
                index=index, field="completed",
            )
        if timestamp + timeframe.value > as_of:
            raise MarketDataError(
                ErrorCode.FUTURE_CANDLE, "candle has not closed at as_of",
                index=index, field="timestamp",
            )
        if timestamp in seen:
            raise MarketDataError(
                ErrorCode.DUPLICATE_TIMESTAMP, "open time already occurred in this batch",
                index=index, field="timestamp",
            )
        if previous is not None:
            if timestamp < previous:
                raise MarketDataError(
                    ErrorCode.OUT_OF_ORDER, "open times must be strictly increasing",
                    index=index, field="timestamp",
                )
            if timestamp - previous != timeframe.value:
                raise MarketDataError(
                    ErrorCode.MISSING_CANDLE, "consecutive opens must be exactly one interval apart",
                    index=index, field="timestamp",
                )
        result.append(MarketCandle(
            symbol=symbol, timeframe=timeframe, timestamp=timestamp,
            **numbers, completed=True,
        ))
        previous = timestamp
        seen.add(timestamp)
    return tuple(result)
