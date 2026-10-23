# Provisional market data, datasets, and frame assembly

Roham owns this package. It normalizes an explicitly documented **internal**
OHLCV input format into immutable `MarketCandle` records. This is not a Wallex
API payload definition, exchange adapter, finalized shared `MarketFrame`, or
trading-readiness check. Python 3.11 standard library only; no dependencies.

The normalizer does no I/O. The dataset loader reads only an explicitly supplied
local file. Neither performs network access, credential loading, scheduling,
order submission, or strategy/risk work. Production code does not import strategy.
Frame assembly is an in-memory operation over two loaded datasets. It produces
an explicitly incomplete internal proposal, not a shared contract.

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

## Internal MarketFrame proposal

`build_market_frame(candles_15m_dataset, candles_1h_dataset, *, base_asset,
quote_asset, as_of)` combines two `LoadedDataset` inputs into a frozen
`MarketFrameProposal`. The local version label is `market-frame-proposal-v0`.
It is **not an approved shared MarketFrame schema**, a Strategy adapter,
CandidateSignal, or authorization to trade. No indicator or strategy is run.

From the repository root:

```python
from pathlib import Path
from wallex.market import Timeframe, TimestampUnit, load_dataset, build_market_frame

fixtures = Path("wallex/market/fixtures")
settings = dict(symbol="SYNTHETIC-BASE-QUOTE",
                timestamp_unit=TimestampUnit.SECONDS, as_of=903600)
m15 = load_dataset(fixtures / "synthetic_frame_m15.jsonl",
                   timeframe=Timeframe.M15, **settings)
h1 = load_dataset(fixtures / "synthetic_frame_h1.jsonl",
                  timeframe=Timeframe.H1, **settings)
frame = build_market_frame(m15, h1, base_asset="BASE", quote_asset="QUOTE",
                           as_of=903600)
assert len(frame.candles_15m) == 21
assert len(frame.candles_1h) == 250
assert frame.snapshot_hash is None
assert frame.data_quality.value == "INCOMPLETE"
```

The two new files are entirely **synthetic**, with constant OHLC values,
M15 base volume 1 and H1 base volume 4. They share a decision cutoff of 903600
UTC Unix seconds; both latest candles close exactly there. They do not represent
any exchange market, account, permitted symbol, or strategy performance. The
base/quote labels in this example are supplied explicitly, not parsed from the
symbol. No cross-timeframe aggregation check or resampling is implemented.

### History validation and freshness

Both inputs must be `LoadedDataset` records with tuple candle collections. Their
exact symbol labels and integer `as_of` values must agree; each dataset's cutoff
must equal the requested frame cutoff. The first input must be M15 and the
second H1. Asset identifiers must be explicit nonempty strings without whitespace
or control characters, and must differ. Case is preserved; no asset registry or
base/quote relationship is inferred from the symbol.

The builder revalidates every candle, including directly constructed/frozen
models. A candle's symbol and timeframe must agree with its dataset, and its
normalized numeric fields must already be Decimal. Existing `normalize_candles`
then checks the full history's values, timestamps, completion, continuity and
cutoff. No raw string/integer numeric coercion is allowed at this normalized
model boundary. Source timestamp-unit metadata is retained by the dataset, but
revalidation always uses **seconds** because its candles are already normalized.
Millisecond source data is never divided by 1000 a second time.

Only after full data validation does each history have its warm-up and freshness
checked. At least **21 consecutive M15** and **250 consecutive H1** completed bars
are mandatory. Extra history is retained without trimming. Latest-close checks
use the caller's explicit decision cutoff, never the wall clock:

```text
latest_close = last_candle.timestamp + interval
expected_latest_close = (as_of // interval) * interval
latest_close must equal expected_latest_close
```

For example, at `as_of=904500` the latest M15 close must be 904500, while the
latest completed H1 close is 903600. An incomplete next H1 bar is not substituted.
A continuous, sufficiently long history can still be stale and is rejected.
Open/future rows reject the entire batch; there is no automatic prefix selection,
sorting, gap filling, deduplication, or repair.

### Output and incomplete quality

| Field | Meaning in this proposal |
| --- | --- |
| `schema_version` | Fixed internal label `market-frame-proposal-v0`. |
| `symbol`, `base_asset`, `quote_asset`, `as_of` | Validated explicit identity and decision cutoff. |
| `candles_15m`, `candles_1h` | Revalidated tuples of normalized immutable `MarketCandle` records. |
| `dataset_sha256_15m`, `dataset_sha256_1h` | Separate original file-byte hash claims copied from each input. |
| `event_time`, `received_time` | Always `None`: frame-level meanings are not agreed. |
| `best_bid`, `best_ask`, `spread_pct` | Always `None`: Quote is unavailable and spread semantics are pending. |
| `depth_valid` | Always `False`: no Order Book evidence was supplied. |
| `snapshot_hash` | Always `None`: canonical frame-hashing policy is not agreed. |
| `data_quality` | Always `FrameQuality.INCOMPLETE` for a successfully built proposal. |
| `quality_reasons` | Immutable tuple describing unavailable data and pending policies. |

The quality reasons are `QUOTE_UNAVAILABLE`, `DEPTH_UNAVAILABLE`,
`FRAME_TIME_SEMANTICS_PENDING`, `SNAPSHOT_HASH_POLICY_PENDING`, and
`SHARED_SCHEMA_PENDING`. Missing financial/time information is not replaced by
zero, candle timestamps, `as_of`, system time, or fabricated quotes.

Successful construction means only the history checks passed. It does not mean
the whole frame is complete, the strategy qualifies, risk passed, or an order
may be sent. The proposal does not expose a trade authorization result. Use the
builder for validated construction; the dataclass itself is an internal output
container. Pending fields and schema/quality labels cannot be supplied as
constructor arguments or changed through ordinary frozen-dataclass assignment.

Dataset digests are checked for lowercase 64-character hexadecimal form and
preserved separately. The builder has no original file bytes and cannot verify
a hand-constructed dataset's claimed digest or source identity. It neither
re-hashes normalized candles as file bytes nor merges the two claims into a
`snapshot_hash`. File-byte provenance and canonical frame identity are distinct.

### Frame errors

`MarketFrameError` exposes `code`, `dataset`, `index` and `field`. The dataset
location is `candles_15m_dataset` or `candles_1h_dataset` when applicable; `index`
is a zero-based candle index, not a file line. Configuration/global mismatches
have no invented candle index. A wrapped `MarketDataError` preserves its original
code/index/field and is chained as `__cause__`.

Frame-specific `FrameErrorCode` values are `INVALID_CONFIG`, `INVALID_DATASET`,
`INVALID_DATASET_HASH`, `SYMBOL_MISMATCH`, `TIMEFRAME_MISMATCH`, `AS_OF_MISMATCH`,
`CANDLE_METADATA_MISMATCH`, `INVALID_NORMALIZED_CANDLE`, `INSUFFICIENT_HISTORY`,
and `STALE_HISTORY`. Insufficient-history errors include the required/available
counts; stale-history errors include actual/expected close times.

Validation stops at the first failure: explicit cutoff/assets, full M15 input
validation/warm-up/freshness, then H1, then cross-dataset symbol equality. Invalid
data raises rather than returning a partial or apparently complete frame.

### Runnable synthetic example and open agreements

```text
python -B -m wallex.market.frame_example
```

This loads both fixture files through `load_dataset`, builds a proposal, and
prints a summary with `evidence_kind="synthetic_fixture"`. It imports/runs no
Strategy, Risk or Execution code. Its fixture paths are relative to the module,
not the working directory. `build_synthetic_frame()` can also be imported from
`wallex.market.frame_example`. Printed JSON is a human-readable summary, not a
canonical serializer or frame-hash specification.

Setayesh, Fatima and Roham still need to agree the shared schema/consumer
adapters, frame versus Quote/Book source/received timestamp semantics, spread
formula/units, canonical serialization and hash coverage, and actual data
sources/markets/assets/API units. The suggested five-second Quote/Book freshness
limit is **not finalized and is not implemented here**. No market selection,
live-mode policy, or shared contract is established by this proposal.

## Tests

From the repository root, without writing bytecode:

```text
python -B -m unittest wallex.market.test_normalization -v
python -B -m unittest wallex.market.test_datasets -v
python -B -m unittest wallex.market.test_frames -v
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

Frame tests cover minimum counts, extra history, exact and intermediate time
boundaries, stale-but-contiguous data, metadata mismatches, hand-constructed bad
models, unchanged inputs, immutable/repeatable outputs, and deliberately missing
fields. The full synthetic example is also run in a subprocess from an unrelated
working directory with an assertion that no strategy/risk/execution module was
imported.

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


## Opt-in public historical research

`python -B -m wallex.market.history --fetch --symbol BTCUSDT --base BTC --quote USDT --start 2026-10-01T00:00Z --end 2026-10-15T00:00Z --volume-unit base --output "$env:TEMP\qf-history"`

Run only after independently confirming UDF timestamps are UTC candle-open seconds
and `v` is base-asset volume: `--volume-unit base` is a caller attestation, not
provider verification. No live response was fetched during implementation. The
accepted wire shape is `s=ok` with equal-length `t/o/h/l/c/v` arrays. Other shapes,
no-data, truncation or different actual spacing fail closed; no protocol fallback
or unit conversion is guessed. Dates are explicit whole UTC hours, start inclusive
and end exclusive, at most 14 days, entirely closed at collection time.

The default M1 path uses public GET `/v1/udf/history` with `symbol/resolution=1/from/to`. Actual
60-second spacing and exact range coverage are checked; `resolution=15` is never
trusted or requested. Complete M1 groups produce M15 and H1 using first open,
maximum high, minimum low, final close and exact volume sums. Existing normalizers
and JSONL loaders validate every result. Default chunks are four hours, at most
96 requests (hard cap 128), 10-second timeouts, 1 MiB responses, no retry/redirect,
no ambient proxy and no credentials. M1 support is confined to ingestion; the
existing Strategy timeframe/closed-candle guards are unchanged.

Output must be a **new directory outside this repository**. Exact raw bodies,
request URLs, receipt UTC seconds and SHA-256 hashes are retained with normalized
JSONL hashes in `manifest.json`; failed collections remain INCOMPLETE. No files
are overwritten. Hashes establish integrity, not provider authenticity or account
eligibility. Downloaded data must never be committed.

`python -B -m wallex.market.history --research --input "$env:TEMP\qf-history"`

This offline command verifies raw/data hashes and source identity, then reuses
ReplayPlan, TP-1 Shadow and candidate reporting. Default research starts after
250 complete H1 bars; shorter collections reject research. Optional whole-hour
`--decision-start/--decision-end` restrict the window. Optional `--pe` is an explicit
provisional maximum BUY price; absent Pe leaves stop validity unknown. Candidate
counts and technical rejection reasons are reported with permanent NO_TRADE,
no executable quantities, approval, exits or profitability claims. Public wire,
timestamp/volume semantics and actual historical results await owner validation.


## Native H1 ingestion (independent of M1)

Use the existing public-only collector with an explicit native H1 flag:

```powershell
python -B -m wallex.market.history --fetch --native-h1 --symbol BTCUSDT --base BTC --quote USDT --start 2026-10-10T00:00Z --end 2026-10-21T00:00Z --volume-unit base --output "$env:TEMP\qf-native-h1"
```

`--volume-unit base` is an explicit **caller assumption**, not independent
verification. Provider volume units remain unverified; the native manifest records
`volume_semantics=CALLER_ATTESTED_BASE` and `volume_unit_verification=UNVERIFIED`.
No conversion or multiplication of native volume occurs. The command is shown
for a caller who explicitly accepts that provisional assumption; it does not
establish units suitable for trading or risk authorization.

Requests use public GET `/v1/udf/history` with `resolution=60`. Actual timestamps
must match every UTC hourly open in `[start, end)` exactly; `end` is exclusive and
all candles must be closed by the observed cutoff. Missing, duplicate, unordered,
misaligned, extra, partial, inconsistent OHLCV and nonfinite data fail closed.
The existing normalizer and JSONL loader validate native H1 directly. No M1
fallback, synthetic bars, sorting, filling or gap-aware M15 reconstruction occurs.
The original strict M1-to-M15/H1 path remains the default and still rejects gaps.

Output is only `h1.jsonl`, exact raw bodies and `manifest.json` in a new directory
outside the repository. Native provenance records resolution `60`, spacing `3600`,
`ingestion=NATIVE_H1`, `aggregation=NONE`, request ranges, receipt timestamps,
byte counts and raw/dataset SHA-256 hashes. Existing request/time/size bounds and
INCOMPLETE failure manifests apply. Never commit downloaded data.

Owner-provided independent QA reported 264 consecutive H1 candles for October
10?21, 2026, and one matching native/M1-aggregate hour on October 20, 12:00?13:00
UTC. Three M1 gaps elsewhere remain unresolved. Those observations are supplied
QA evidence, not a live verification performed by this implementation. Tests use
synthetic injected responses only. Native H1 collection does not supply M15 and
is deliberately not accepted by the existing combined-history research command.
Contiguous-window research and M15 gap handling are separate future tasks.
No Strategy, Risk, Execution, account or trading behavior is changed.


## Offline gap-aware M15 diagnostics

`gap_aware.analyze_minutes(raw_bytes, symbol=..., base_asset=..., quote_asset=...,
start=..., end=..., as_of=..., volume_unit=None)` analyzes saved UDF M1 bytes;
no import or command makes network requests. UTC Unix-second `start` is inclusive,
`end` exclusive, both minute-aligned; the explicit `as_of` must close the entire
range. Limits: 14 days and a 1 MiB input, matching existing ingestion bounds.

```powershell
python -B -m wallex.market.gap_aware --analyze --input SAVED_RAW_JSON --symbol BTCUSDT --base BTC --quote USDT --start START_UTC_SECONDS --end END_UTC_SECONDS --as-of CUTOFF_UTC_SECONDS --summary-only
```

Replace placeholders with the saved request's actual range/cutoff; never infer
coverage from the first/last returned candle. Omit `--summary-only` to include
provisional M15 candle bodies. This reads the input unchanged and writes only JSON
to stdout. Never commit downloaded data or reports containing real datasets.

Every present row is validated through unchanged strict normalization of its
contiguous source run, even inside an incomplete bucket. Only exactly 15 valid,
consecutive UTC minute bars produce a M15 candle through the existing aggregator.
Missing timestamps and incomplete buckets are explicit; consecutive M15 segments
are separate and must never be concatenated across gaps. At partial range edges,
`outside_expected_range` distinguishes unrequested minutes from missing source
minutes. Duplicates, ordering/alignment errors, invalid OHLCV, nonfinite values,
incomplete/future observations and inconsistent arrays reject the analysis.

Present zero-volume bars keep their original OHLC; they are never treated as
missing or filtered out. The supplied carry-forward hypothesis is **not** adopted
as an exchange rule. Volume units default to `UNVERIFIED`; optional
`--volume-unit base` records `CALLER_ATTESTED_BASE`, without conversion or
independent verification. Numeric validator reuse does not attest source units.
Raw byte SHA-256/size and explicit instrument/range metadata preserve provenance;
source identity remains unauthenticated, including when an echoed symbol matches.

Synthetic QA-pattern test: 600 expected M1, 597 present, 3 missing, 47 present
zero-volume bars, 38 complete M15, 2 incomplete buckets and 2 separate 19-bar runs.
The missing times are `1791845940`, `1791846000`, `1791846060`; incomplete bucket
opens are `1791845100` and `1791846000`. This reproduces supplied findings with
synthetic bytes, not a new fetch or a claim about native H1 price equivalence.

Partial coverage is `PARTIAL`; even complete coverage is only
`COVERAGE_COMPLETE_RESEARCH_ONLY`. All reports retain `fully_validated=False`,
`NO_TRADE` and `executable_ready=False`. This is not a LoadedDataset, shared schema
or TP-1 adapter. Strict `history.decode_minutes()`, native H1 ingestion, existing
research/replay, Strategy, Risk, Shadow and Execution behavior are unchanged.
