# Provisional market-data normalization

Roham owns this package. It normalizes an explicitly documented **internal**
OHLCV input format into immutable `MarketCandle` records. This is not a Wallex
API payload definition, exchange adapter, finalized shared `MarketFrame`, or
trading-readiness check. Python 3.11 standard library only; no dependencies.

The normalizer does no I/O. The dataset loader reads only an explicitly supplied
local file. Neither performs network access, credential loading, scheduling,
order submission, or strategy/risk work. Production code does not import strategy.

## Interface and raw format

```python
from wallex.market import Timeframe, TimestampUnit, normalize_candles

raw = [{
    "timestamp": 0,
    "open": "100", "high": "101", "low": "99", "close": "100",
    "volume": "0", "completed": True,
}]
bars = normalize_candles(
    raw,
    symbol="TEST-BASE-QUOTE",        # Synthetic label, not an exchange symbol.
    timeframe=Timeframe.M15,
    timestamp_unit=TimestampUnit.SECONDS,
    as_of=900,
)
assert bars[0].timestamp == 0
assert str(bars[0].close) == "100"
assert str(bars[0].volume) == "0"
assert bars[0].completed is True
```

All four keyword arguments are mandatory, including for empty input. Supply
`Timeframe.M15` (900 seconds) or `Timeframe.H1` (3600 seconds), and a
`TimestampUnit` enum member, not an implicit integer/string timeframe or unit.
`symbol` is an opaque, case-preserved nonempty string without whitespace;
the normalizer does not verify it against an exchange instrument list.

`records` is a finite sequence (e.g. list or tuple) of mappings, one instrument
and timeframe per call. Generators are not accepted. Each mapping has **exactly**
the following keys; missing or additional fields are errors. An upstream adapter
must explicitly map provider payloads to this schema and verify instrument and
timeframe identity. In particular, per-row symbol/timeframe fields are rejected,
not silently overwritten by the batch configuration.

| Field | Accepted input and units |
| --- | --- |
| `timestamp` | Nonnegative Python `int`, excluding `bool`; candle **open** time since Unix epoch in the explicitly selected unit. |
| `open`, `high`, `low`, `close` | Finite `Decimal`, Python `int` excluding `bool`, or ASCII decimal text; positive prices in the instrument's agreed quote unit. |
| `volume` | Same numeric types; finite, nonnegative **base-asset** volume. Zero is valid. Quote volume must be converted upstream using an agreed policy. |
| `completed` | Actual Python `bool`; only `True` is consumable. Never inferred from the timestamp. |

Decimal text accepts an optional sign, decimal point, and exponent, for example
`"100"`, `"+100.50"`, `".125"`, `"1."`, `"1.01e2"`. Whitespace, underscores,
non-ASCII digits, NaN, infinity, floats, and booleans are rejected. Conversion is
exact and independent of caller Decimal precision/traps; no quantization or
price/volume rounding takes place. Negative zero volume compares equal to zero
and is accepted. Zero or negative-zero prices are rejected.

`TimestampUnit.MILLISECONDS` must describe whole seconds (`timestamp % 1000 == 0`);
subsecond values are rejected, never truncated. The output is always integer
**UTC Unix seconds**, with no local timezone conversion. Units are applied
literally and never guessed from magnitude. `as_of` is always a nonnegative
Python `int` in UTC Unix **seconds**, even with millisecond input.

## Validation and output

`normalize_candles(...) -> tuple[MarketCandle, ...]` returns immutable containers
with `symbol`, `timeframe`, `timestamp`, `open`, `high`, `low`, `close`, `volume`,
and `completed`. Numeric output fields are `Decimal`; all returned records have
`completed=True`. Use the normalizer to create validated records. The dataclass
constructor itself is only a container and does not validate arbitrary direct
construction.

The whole batch is accepted or the first failure raises `MarketDataError`;
there is no returned partial result. The input mappings/sequence are not mutated.
Records are never sorted, removed, deduplicated, or forward-filled. Equal input
and explicit settings produce equal output. Empty history produces `()` and
does not establish indicator warm-up or trading readiness.

Validation checks configuration first, then records in input order. Each row
is checked for required/unknown fields, timestamp type/unit/grid, numeric values,
positive prices/nonnegative volume, OHLC consistency, completion/cutoff, then
duplicate/order/continuity. Required fields are checked in the table's order.
The OHLC rule is `low <= open <= high` and `low <= close <= high`.

Each open must align to `timestamp % timeframe.value == 0`. Consecutive opens
must differ by exactly the selected interval. A repeated open, including a
nonadjacent repeat, is a duplicate; another backwards timestamp is out of order.
Missing intervals reject the batch; a price gap between consecutive valid
candles is allowed. Continuity is checked within a call, not across separate
batches; leading/trailing missing history cannot be inferred without a requested
range.

An open candle (`completed=False`) rejects the entire batch, even if its nominal
end time has passed. A completed candle is still rejected if
`timestamp + timeframe.value > as_of`. A close **exactly** at `as_of` is allowed.
Callers must deliberately select the intended completed historical prefix before
normalization; nothing is silently filtered. No wall clock is consulted. Old
historical data is allowed: this checks availability at a cutoff, not freshness.

## Errors

```python
from wallex.market import ErrorCode, MarketDataError

try:
    normalize_candles(
        [dict(raw[0], completed=False)],
        symbol="TEST-BASE-QUOTE", timeframe=Timeframe.M15,
        timestamp_unit=TimestampUnit.SECONDS, as_of=900,
    )
except MarketDataError as error:
    assert error.code is ErrorCode.INCOMPLETE_CANDLE
    assert error.index == 0       # Zero-based index; None for batch/config errors.
    assert error.field == "completed"
    print(error)
# INCOMPLETE_CANDLE at records[0].completed: open candles are not consumable
```

Codes are stable enum values. `field` is `None` for a malformed record, unknown
fields, or an invalid collection; OHLC cross-field failures use `"ohlc"`.

| Codes | Meaning |
| --- | --- |
| `INVALID_CONFIG` | Invalid symbol, timeframe, unit, or cutoff; `field` identifies the argument. Missing mandatory arguments raise Python `TypeError`. |
| `INVALID_RECORDS`, `INVALID_RECORD` | Unsupported collection or non-mapping row. |
| `MISSING_FIELD`, `UNEXPECTED_FIELD` | Required key absent or unsupported key present. |
| `INVALID_TIMESTAMP`, `SUBSECOND_TIMESTAMP`, `UNALIGNED_TIMESTAMP` | Invalid timestamp type/value, nonintegral seconds, or wrong UTC grid. |
| `INVALID_NUMBER`, `NON_POSITIVE_PRICE`, `NEGATIVE_VOLUME`, `INVALID_OHLC` | Invalid numeric representation or domain. |
| `INVALID_COMPLETION`, `INCOMPLETE_CANDLE`, `FUTURE_CANDLE` | Invalid completion type, open candle, or close after cutoff. |
| `DUPLICATE_TIMESTAMP`, `OUT_OF_ORDER`, `MISSING_CANDLE` | Repeated, backwards, or discontinuous open times. |

## Existing indicator compatibility

The market and strategy records remain separate internal models. At a consumer
boundary, explicitly project a normalized single-instrument/timeframe batch into
the existing strategy `Candle` record. This deliberately omits volume and symbol,
which the current indicator interface does not carry. Preserve their provenance
outside that interface; do not combine batches or pass another timeframe/cutoff.

```python
from wallex.strategy.indicators import Candle, Timeframe as IndicatorTimeframe, ema

indicator_bars = tuple(Candle(
    timestamp=b.timestamp, open=b.open, high=b.high, low=b.low,
    close=b.close, completed=b.completed,
) for b in bars)
points = ema(indicator_bars, 1, timeframe=IndicatorTimeframe.M15, as_of=900)
assert str(points[0].value) == "100"
```

This is an explicit integration example, not automatic strategy wiring. Market
normalization does not enforce EMA/ATR history minima or TP-1's 250 H1 bars.

## Local JSONL datasets

`load_dataset(path, *, symbol, timeframe, timestamp_unit, as_of)` reads a local
dataset for reproducible testing and future replay. All keyword arguments are
mandatory and have the same meaning as for `normalize_candles`. No strategy is
run, and no backtest or execution mode is started.

```python
from pathlib import Path
from wallex.market import Timeframe, TimestampUnit, load_dataset

dataset = load_dataset(
    Path("wallex/market/fixtures/synthetic_m15.jsonl"),
    symbol="SYNTHETIC-BASE-QUOTE",
    timeframe=Timeframe.M15,
    timestamp_unit=TimestampUnit.SECONDS,
    as_of=2700,
)
assert dataset.candle_count == 3
assert (dataset.first_open_at, dataset.last_open_at) == (0, 1800)
assert str(dataset.candles[1].volume) == "1.25"
assert len(dataset.sha256) == 64
```

The bundled `fixtures/synthetic_m15.jsonl` is **entirely synthetic**, with a
caller-supplied synthetic instrument label. Its observations are not real market
data and do not demonstrate strategy performance.

### File format

- Strict UTF-8 without a BOM. No encoding autodetection or replacement characters.
- One JSON object per physical LF-delimited line; CRLF is accepted too.
  A single terminal newline is optional, not an empty record. Blank/whitespace
  lines, including an additional terminal blank line, are errors.
- Each object has exactly the raw-format fields documented above. JSON comments,
  multiple objects on one line, and top-level arrays/scalars are rejected.
- `timestamp` is a JSON integer and `completed` is a real JSON boolean.
  Prices/volume may be decimal strings or JSON integers. Fractional/exponent
  **JSON number tokens** such as `100.0` or `1e2` are forbidden; use `"100.0"` or
  `"1e2"` strings instead. JSON NaN/Infinity tokens and duplicate keys (including
  escaped equivalents) are explicitly rejected.
- A zero-byte file is valid empty history, subject to valid configuration.
- One configured symbol/timeframe per file; source identity must be verified
  upstream. The loader does not infer metadata from filenames or invent a
  cross-instrument file format.

The file is read once into bytes. Those same bytes supply both UTF-8/JSON parsing
and SHA-256. After parsing, the **whole** collection is passed to the existing
normalizer once, so gaps, duplicates, ordering, closed-candle and cutoff checks
apply across records. Its rules are unchanged. Rows are not repaired or skipped.

### Result and reproducibility

`LoadedDataset` is an immutable internal result container, not a finalized
`MarketFrame`. Construct validated results via `load_dataset`. It exposes:

| Attribute | Meaning |
| --- | --- |
| `candles` | Tuple of normalized immutable `MarketCandle` records. |
| `sha256` | Lowercase hexadecimal SHA-256 of the exact file bytes read. |
| `candle_count` | Number of normalized candles. |
| `first_open_at`, `last_open_at` | First/last candle open in UTC Unix seconds; `None` for empty history. |
| `symbol`, `timeframe`, `timestamp_unit`, `as_of` | Explicit settings used for this load. |

Count and range are derived from the candles to avoid inconsistent metadata.
`timestamp_unit` records the **input** setting; candle timestamps and range
metadata are always seconds. The result does not depend on the filename, path,
local clock or modification time. The source file is not modified.

SHA-256 identifies **bytes**, not semantic candle equality. Whitespace, LF/CRLF,
or a terminal newline can change the hash while yielding equal candles. File
hash alone is not an experiment identity: record load settings and, for future
backtests, strategy version and all relevant simulation parameters as well.

### Dataset errors and limits

`DatasetError` exposes `code`, `line_number`, `index`, and `field`. Physical line
numbers are **one-based**. A wrapped `MarketDataError` retains its original
`ErrorCode`, zero-based `index`, and `field`, with `line_number = index + 1`.
Its original exception is available as `__cause__`. Configuration errors retain
`index=None` and `line_number=None`; no record location is invented.

Loader-specific `DatasetErrorCode` values are `READ_ERROR`, `INVALID_ENCODING`,
`BLANK_LINE`, `INVALID_JSON`, `DUPLICATE_KEY`, `INVALID_JSON_NUMBER`, and
`NON_OBJECT_RECORD`. Read, decode, JSON parser and normalization failures preserve
their original exceptions through chaining when one exists. Explicit format
checks such as a BOM/blank line do not have an underlying exception. Loader-only
failures leave `index` and `field` as `None`.

```python
from wallex.market import DatasetError, ErrorCode

# Catch DatasetError around load_dataset(...).
# For example, a negative volume on physical line 2 has:
# error.code is ErrorCode.NEGATIVE_VOLUME
# error.line_number == 2, error.index == 1, error.field == "volume"
# isinstance(error.__cause__, MarketDataError)
```

Failure precedence follows phases: read file, decode the complete UTF-8
snapshot, parse lines in order, then normalize in record order (including
configuration validation). The first failing phase stops the load; no partial
dataset is returned. For example, a later JSON syntax failure is reported before
an earlier row's OHLC error, because normalization starts only after parsing.

The loader holds the complete byte snapshot, decoded text, and records in
memory. Streaming, file-size limits, concurrent-writer coordination and dataset
storage/versioning are not implemented. JSON parsing is subject to Python's
parser limits; limit failures are reported as `INVALID_JSON`. An upstream owner
must ensure a stable source file when a snapshot of a specific historical
version is required. Hashing records what was read, not authenticity or source
instrument identity.

## Tests

From the repository root, without writing bytecode:

```text
python -B -m unittest wallex.market.test_normalization -v
python -B -m unittest wallex.market.test_datasets -v
python -B -m unittest discover -s wallex -p "test_*.py" -v
```

Synthetic fixtures cover numeric types and exactness, timestamp units, both
timeframes, cutoff boundaries, open/future rejection, malformed/missing data,
duplicates/order/gaps, immutability and repeatability. A consumer compatibility
test checks actual EMA20, EMA50 and ATR14 outputs and their availability times
on normalized M15 and H1 data, without changing strategy files.

Dataset tests use temporary local files and fixed synthetic byte fixtures. They
cover exact hashes and a single read snapshot, metadata/settings, line endings,
repeatability/immutability, strict JSON/encoding errors, portable read failures,
whole-file continuity and cutoff checks, error provenance, and the bundled
synthetic fixture.

## Team decisions still required

- Final shared `MarketFrame` schema, ownership/versioning, and consumer adapters.
- Provider payload mapping, verified instrument identity, base/quote units,
  precision/minimum quantities, and any IRR/IRT conversion.
- Provider completion semantics, source timestamps and permitted freshness/latency.
- Session/outage gaps, requested history ranges, cross-batch continuity,
  resampling, and warm-up provisioning. This version assumes contiguous UTC bars.
- Retrieval, pagination/rate limits, retries, persistence and dataset provenance.

None of those policies is inferred here; no finalized shared contract or
permission to trade is established by successful normalization.
