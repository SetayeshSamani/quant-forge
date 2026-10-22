# TP-1 strategy and technical indicators

Purpose: deterministic indicators, H1 trend and M15 pullback eligibility,
and proposed initial stops for the TP-1 Cost-Aware Trend Pullback strategy. The foundation engine remains a
scaffold: every call to
`engine.evaluate()` returns `NO_TRADE`, including when placeholder inputs are
valid. Indicators do not generate confirmed signals or authorize entries.

## Ownership and provisional interfaces

Setayesh owns `wallex/strategy/`, `wallex/risk/`, and `wallex/infra/`.
This task changes only strategy files. Shared contracts and teammate-owned
market, backtest, shadow, execution, and audit modules remain untouched.

`indicators.py` provides frozen internal records, not a shared MarketFrame:

- `Candle(timestamp, open, high, low, close, completed)`: `timestamp` is the
  candle **open** time in UTC Unix **seconds**, as a nonnegative integer.
  Milliseconds, floating timestamps, booleans, and datetime objects are not
  accepted/coerced. The caller must supply the correct units; integer units
  cannot be inferred automatically from magnitude alone.
- OHLC prices must be positive, finite `Decimal` values with
  `low <= open <= high` and `low <= close <= high`. No float/string coercion.
- `Timeframe.M15` is 900 seconds; `Timeframe.H1` is 3600 seconds. Timeframes
  are required explicitly. Open times must align to the UTC interval grid.
- Each function requires an explicit integer `as_of` decision cutoff in UTC
  Unix seconds. A bar is usable only when `completed is True` **and**
  `timestamp + interval <= as_of`. No wall clock or inferred completion.
- `IndicatorPoint(closed_at, value)` records when each result is available;
  `closed_at` is a UTC Unix-second close time, not an open time.

`ema(candles, period, *, timeframe, as_of)`, `true_ranges(candles, *, timeframe,
as_of)`, and `atr(candles, *, timeframe, as_of)` return chronological tuples of
points. Inputs are sequences of Candle records, snapshotted without mutation.
Import directly from `wallex.strategy.indicators`; existing engine exports
and provisional `StrategyInput` / `StrategyDecision` remain unchanged.

## Definitions and initialization

EMA uses closing prices and a configurable positive integer period N. The
first output is the SMA of the first N closes, at the Nth candle's close.
With `alpha = 2 / (N + 1)`, later outputs use
`EMA = previous_EMA + alpha * (current_close - previous_EMA)`.
EMA20 and EMA50 therefore require 20 and 50 completed candles respectively.

True Range is `max(high-low, abs(high-previous_close), abs(low-previous_close))`.
For the **first supplied candle**, prior close is unavailable, so TR is
**high-low**. No prior close is fabricated. Later TRs account for price gaps;
missing time bars are a separate validation failure.

ATR is fixed to Wilder period 14. Its seed is the arithmetic mean of the
first 14 valid TRs, including the first-bar high-low convention. The first
output occurs at the 14th candle close. Later outputs use
`ATR = (previous_ATR * 13 + current_TR) / 14`.

Arithmetic uses a private Decimal context with **34 significant digits**,
`ROUND_HALF_EVEN`, and exponent bounds -999999 through 999999, independent of
the caller's context. Nonterminating fractions are rounded at this precision;
overflow, invalid arithmetic, and underflow raise `IndicatorArithmeticError`.
Outputs are not quantized to exchange tick sizes. No floats are introduced.

## History, continuity, and closed-candle safety

`InsufficientHistory` exposes `required` and `available` counts. There are no
padded zeros or partial seeds. `true_ranges` needs at least one bar; EMA needs
N bars; ATR needs 14. `validate_candles` accepts empty history, while indicator
operations enforce their own minima.

The full TP-1 strategy requires **at least 250 completed H1 candles**. This is
separate from individual mathematical minima. `validate_tp1_warmup(candles,
*, as_of)` validates H1 continuity and enforces 250 bars without generating a
signal. Passing warm-up does not establish strategy readiness or permission.

Duplicate timestamps, unsorted observations, missing rows/fields, nonfinite
OHLC, inconsistent OHLC, incomplete bars, and future close times raise
`InvalidCandleData`. Consecutive opens must differ by exactly the selected
interval. Missing candles are rejected, never invented or forward-filled.
A price gap between consecutive valid bars is allowed and included in TR.

Indicators use only current and earlier prices for each point. A later H1
candle cannot enter an earlier M15 decision merely because its completion
flag is true. A batch containing bars beyond `as_of` is rejected in full;
the caller must select the appropriate completed prefix. No future records
are silently discarded. H1 aggregation/resampling is not implemented.

## H1 trend filter

Import `assess_hourly_trend` from `wallex.strategy.trend`. Its provisional
interface is `assess_hourly_trend(candles=None, *, as_of) -> TrendAssessment`.
The explicit cutoff uses the same UTC Unix seconds as the indicator interfaces.
This filter checks only these two strict conditions, both of which must hold:

```text
EMA20[h] > EMA50[h]
EMA20[h] > EMA20[h-3]
```

Here h is the latest supplied completed H1 candle. Both EMA series reuse the
existing SMA-seeded implementation and its fixed Decimal context. EMA20 and
EMA50 mathematically need 20 and 50 bars, but the trend filter always requires
at least **250 completed, contiguous H1 candles** before evaluating either
condition. The EMA20 slope reference is exactly three completed H1 bars before
the latest one (10,800 seconds between close times). It is not a comparison
against the last one or two bars, and does not require each intermediate bar
to have a rising EMA20. Equality in either condition is a rejection.

`TrendAssessment` is frozen and contains `eligible`, a tuple of `TrendReason`
values, and the evidence fields `ema20`, `ema50`, `ema20_three_bars_ago`,
`latest_closed_at`, and `slope_reference_closed_at`. Evidence timestamps are
candle **close** times in UTC Unix seconds. Passing both conditions reports
`TREND_CONDITIONS_MET`. Valid but ineligible data reports
`EMA20_NOT_ABOVE_EMA50` and/or `EMA20_NOT_RISING_THREE_BARS`, in that order.
Those results retain the actual calculated EMA evidence.

Missing/malformed data, invalid OHLC, duplicate/unsorted/unaligned timestamps,
missing H1 bars, incomplete candles, or a bar closing after `as_of` produce
`eligible=False` with `INVALID_DATA`. Short valid history produces
`INSUFFICIENT_HISTORY`; unrepresentable indicator arithmetic produces
`INDICATOR_ARITHMETIC_ERROR`. These failures contain no invented EMA evidence.
A future or incomplete row rejects the entire input rather than being silently
dropped. Nothing is forward-filled, resampled, or fetched externally.

A close exactly at `as_of` is available; a close one second later is not.
The caller must supply the completed prefix for the intended decision. The
filter does not read the wall clock or infer freshness: old but otherwise valid
history remains evaluable. A future MarketFrame adapter must enforce the
team-approved freshness policy, instrument identity, H1 interval, and timestamp
semantics. The local Candle record and TrendAssessment are not shared contracts.

Trend eligibility is not a pullback signal, CandidateSignal, AgentDecision,
RiskDecision, or executable TradeIntent. Cost checks, stop-loss calculations,
position sizing, and exchange execution are outside this filter. The existing
`engine.evaluate()` still always returns `NO_TRADE`; its contracts and behavior
are unchanged. No risk, infra, or teammate-owned module is modified.

## M15 pullback detector

Import `assess_pullback` from `wallex.strategy.pullback`. Its provisional local
interface is `assess_pullback(candles=None, *, as_of) -> PullbackAssessment`.
The latest completed M15 bar is t. All three conditions must hold together:

```text
Close[t-1] <= EMA20[t-1]
Close[t] > EMA20[t]
0 < Close[t] - EMA20[t] <= ATR14[t]
```

The previous-close equality is **allowed**. Current-close equality is
**rejected**. A positive distance **exactly equal to current ATR14 is allowed**;
a greater distance rejects eligibility. Flat prices with zero ATR do not
qualify. Invalid/nonfinite or negative indicator outputs fail closed.

At least **21 completed, contiguous M15 candles** are required: SMA-seeded EMA20
needs 20 closes for its seed and one more for both current and previous values.
The existing Wilder ATR14 supplies the current ATR using its documented
first-bar and initialization conventions. Indicators are matched by candle
**close timestamp**, so their different warm-up lengths cannot shift the
comparison to the wrong bar. The module does not implement alternative EMA/ATR
calculations and does not modify the existing indicators.

Both the indicators and the detector's price-minus-EMA subtraction use an
explicit Decimal context: 34 significant digits, ROUND_HALF_EVEN, exponent
bounds -999999 through 999999, and explicit arithmetic traps. Caller precision,
rounding, and traps are isolated. Prices never pass through float conversion.
The distance is the subtraction of the calculated EMA under this same fixed
precision; values are not quantized to instrument tick sizes.

`PullbackAssessment` is frozen. It contains `eligible`, an immutable tuple of
`PullbackReason` values, `decision_closed_at`, `previous_closed_at`,
`current_close`, `previous_close`, `current_ema20`, `previous_ema20`,
`current_atr14`, and `distance_from_ema20`. Timestamps are UTC Unix **seconds at
candle close**. The decision timestamp is the latest candle's close, not a later
`as_of` cutoff. The input Candle timestamp remains the candle **open** time.

Valid but nonqualifying inputs retain actual evidence and explicit reasons for
the failed crossover, nonpositive ATR/distance, or excessive distance. Invalid
input, insufficient history, arithmetic failures, or missing indicator-time
alignment produce `eligible=False`, an explicit failure reason, and no price or
indicator evidence. No zeros or fabricated timestamps stand in for missing data.

The explicit `as_of` cutoff and existing M15 validation reject incomplete or
future bars, invalid OHLC, unaligned/duplicate/unsorted timestamps, and missing
intervals. A bar closing exactly at `as_of` is usable; one closing later is not.
The entire batch is rejected rather than silently dropping future rows. No
candles are invented, forward-filled, or fetched, and no wall clock is read.
The caller must supply the appropriate completed prefix and enforce any future
team-approved freshness policy.

This result establishes **M15 pullback eligibility only**. The complete strategy
also needs an eligible H1 assessment from `trend.py` at the appropriate decision
cutoff, with its separate 250-H1 warm-up requirement. Combining these results
into an approved signal is not implemented here. **MarketFrame and
CandidateSignal contracts remain pending**; these local records do not define
or change a team-wide contract. Adapters must agree instrument identity,
timestamp semantics, timeframe metadata, freshness, and cross-timeframe alignment.

The detector creates no TradeIntent, orders, risk sizing, stop-loss levels, AI
agent decisions, or live execution. H1 eligibility plus M15 eligibility still
does not authorize trading without the remaining strategy, risk, and execution
checks. `engine.evaluate()`, `trend.py`, and `indicators.py` are unchanged.

## Proposed initial stop levels

Import `assess_initial_stop` from `wallex.strategy.stops`. The provisional local
interface is `assess_initial_stop(candles=None, *, entry_price, as_of) ->
StopAssessment`. It calculates proposed initial levels for **long spot candidates
only**. Both keyword arguments are mandatory. `entry_price` must be a positive,
finite `Decimal`; floats, strings, integers, booleans, missing values, and
nonfinite prices are rejected without coercion. There is **no fallback to the
latest candle close**, and a caller-supplied price is not proof of a fillable entry.

`entry_price` represents Pe, the maximum permitted BUY price, supplied explicitly
by the caller. Pe is not automatically the latest candle closing price and is
not necessarily the final fill price. Its source and freshness must be validated
by the future market/execution integration. Stop calculations do not authorize
trading. Risk sizing must use the approved Pe and applicable cost assumptions.

For the latest completed M15 candle t:

```text
lowest_low = min(Low[t], Low[t-1], Low[t-2])
structural_stop = lowest_low - 0.1 * ATR14_M15[t]
volatility_stop = entry_price - 1.5 * ATR14_M15[t]
initial_stop = min(structural_stop, volatility_stop)
risk_per_unit = entry_price - initial_stop
```

The three-candle lookback includes the decision candle; earlier lows are
excluded even when lower. The **lower** stop is selected, including when both
candidates are equal. A structural candidate above entry does not invalidate a
lower valid volatility candidate; the postconditions apply to the selected stop.

At least **14 completed, contiguous M15 candles** are required for the existing
Wilder ATR14. Its initialization and first-bar TR convention are unchanged.
ATR is selected by the latest candle's **close timestamp**, not an assumed output
index. The input timestamps remain UTC Unix-second **open** times; `as_of` is an
explicit integer UTC Unix-second cutoff, and `decision_closed_at` is the latest
candle close, even when the cutoff is later. Incomplete/future bars, inconsistent
OHLC, duplicate/unsorted/unaligned timestamps, and gaps reject the whole input.
No sorting, forward filling, invented prices, data fetching, or wall clock is used.
Old but valid history does not become fresh by passing this calculation.

ATR and stop arithmetic use the same fixed Decimal settings: 34 significant
digits, ROUND_HALF_EVEN, fixed exponent bounds -999999 through 999999, and
explicit arithmetic traps independent of caller settings. No float conversion
or exchange tick-size rounding occurs. Validate the rounded calculation:
`ATR > 0`, `0 < initial_stop < entry_price`, and `risk_per_unit > 0` must all hold.
Precision collapse to entry/zero risk, nonpositive stops, invalid entry/ATR,
insufficient history, alignment errors, and arithmetic failures all fail closed.

`StopAssessment` is frozen and contains `valid`, an immutable tuple of
`StopReason` codes, `decision_closed_at`, `entry_price`, `lowest_low_last3`,
`atr14`, `structural_stop`, `volatility_stop`, `initial_stop`, and `risk_per_unit`.
Every failure has `valid=False` with explicit reasons and **all evidence fields
None**. Invalid or partial levels are never returned for downstream use.
A successful result reports `LEVELS_VALID`; that means only these calculations
and postconditions succeeded.

This is not position sizing: risk per unit is an absolute price difference,
not a quantity, portfolio risk allocation, loss cap, or risk approval. Fees,
slippage, liquidity, price/quantity increments, minimum notionals, stop-order
support, and exchange constraints are not checked. Market-rule/tick rounding
needs an explicit approved policy and revalidation; these unrounded levels must
not be submitted directly as exchange orders.

MarketFrame/CandidateSignal integration, instrument and quote-unit identity,
entry-price provenance, freshness policy, and synchronized decision cutoffs
remain pending. H1 trend and M15 pullback eligibility are separate requirements;
this helper neither combines them nor authorizes a trade. It creates no
TradeIntent, order, agent decision, risk approval, or live execution. Existing
engine, indicator, trend, pullback, risk, infra, audit, and execution code is
unchanged.

## Provisional MarketFrame diagnostics

`wallex.strategy.frame_adapter.assess_market_frame(frame=None, *, pe=None)` is a
Strategy-owned, read-only local adapter for **market-frame-proposal-v0**. It does
not import `wallex.market`, which is currently available only on the teammate
Market feature stack. No teammate branch needs to be merged to import or test
this adapter. This interface is provisional and is not an approved shared schema.

The input is a passive attribute record with the following boundary. Methods,
provider payloads, dictionaries, lazy/live readers, and concurrent mutation are
outside the interface; obtain a stable snapshot upstream. The adapter never
assigns to source objects. Ordinary attribute/validation failures reject the
frame without exposing foreign exception text. This is not a sandbox for objects
with active properties.

| Required attributes | Local meaning |
| --- | --- |
| `schema_version` | Exact string `market-frame-proposal-v0`; all other versions reject. |
| `symbol`, `base_asset`, `quote_asset` | Explicit nonempty strings without whitespace/control characters; distinct assets. Case/units are preserved, not inferred from the symbol. |
| `as_of` | Nonnegative integer UTC Unix-second decision cutoff, excluding bool. |
| `candles_15m`, `candles_1h` | Tuples of normalized candle-like attribute records. |
| `dataset_sha256_15m`, `dataset_sha256_1h` | Separate lowercase 64-character hexadecimal file-byte hash claims. |
| `data_quality`, `quality_reasons` | `INCOMPLETE` and the five v0 quality reasons, as strings or Enum members with string values; reasons must be a tuple. |
| `event_time`, `received_time`, `best_bid`, `best_ask`, `spread_pct`, `snapshot_hash` | Required and explicitly `None` for this version. |
| `depth_valid` | Required and exactly `False`. |

Each candle must expose `symbol`, `timeframe`, `timestamp`, `open`, `high`, `low`,
`close`, `volume`, and `completed`. Its symbol must match the frame. Timeframe is
an Enum with name `M15`/`H1` and exact integer value 900/3600 for the corresponding
history; raw strings/interval numbers are not coerced. OHLC must already be
positive finite Decimals, volume a nonnegative finite Decimal in **base-asset
units**, and completion exactly `True`. Timestamps are normalized UTC Unix-second
**open** times, even if the source dataset used milliseconds. No second unit
conversion takes place.

Projection creates existing Strategy `Candle` records without rounding or float
conversion. It validates OHLC, open-time grid, chronological order, continuity,
and `open + interval <= as_of` using the existing validator. Both histories are
fully validated before any technical assessment is run. At least 21 M15 and
250 H1 completed candles are required, and each latest close must equal:

```text
expected_latest_close = (as_of // interval) * interval
```

Thus at an intermediate M15 cutoff the H1 assessment uses the previous completed
hour. The same frame cutoff is passed to the unchanged `assess_hourly_trend`,
`assess_pullback`, and, when requested, `assess_initial_stop`. Nothing is sorted,
filtered, resampled, forward-filled, or invented. No wall clock is consulted.
Freshness here is relative to the supplied cutoff, not proof of present-time
quote or book freshness.

The frozen `FrameDiagnostics` contains `frame_valid`, local reason codes,
optional `FrameMetadata`, Trend/Pullback assessments, optional StopAssessment,
`technical_eligible`, and a safe rejection location. Metadata preserves identity,
cutoff/schema, both dataset digest claims, all quality reasons and unavailable
market fields. Each frozen `HistoryDiagnostics` retains the exact projected
candles, matching symbol, explicit Strategy timeframe, and aligned
`volumes_base[i]` for `candles[i]`. This keeps volume and provenance outside the
smaller indicator Candle interface rather than silently discarding them.

`frame_valid=True` means only local validation succeeded. `technical_eligible`
means **Trend AND Pullback** passed; it is independent of optional Pe/stop validity.
For invalid/unsupported frames the result has no metadata, candles, or assessment
evidence, `technical_eligible=False`, and an explicit rejection reason. Technical
condition failures on valid histories retain their actual diagnostic evidence.
All results have fixed `executable_action=Signal.NO_TRADE` and
`executable_ready=False`; neither can be enabled through constructor arguments.
The existing `engine.evaluate()` is unchanged and continues to return NO_TRADE.

Optional `pe` is the caller's **provisional maximum permitted BUY price**, supplied
as a positive finite Decimal. It is never derived from candle close or missing
Quote data. Absent Pe reports `PE_UNAVAILABLE` without calculating stops; invalid
Pe reports `PE_INVALID` and also skips stops. Valid numeric Pe reports
`PE_PROVISIONAL` and permits only a proposed stop calculation. If stop validation
fails, `STOP_INVALID` accompanies the unchanged fail-closed StopAssessment with
no usable levels. A mathematically valid stop does not approve Pe provenance,
freshness, instrument/tick rules, or fee/slippage assumptions. Risk sizing must
use the independently approved Pe and applicable costs.

The v0 deficiencies always block executable readiness: incomplete quality,
unavailable Quote/Depth, unknown frame event/received times, missing canonical
snapshot hash, and unapproved shared schema. Contradictory claims such as a
populated Quote or `depth_valid=True` reject this version instead of silently
upgrading it. Its five quality reasons are preserved: `QUOTE_UNAVAILABLE`,
`DEPTH_UNAVAILABLE`, `FRAME_TIME_SEMANTICS_PENDING`,
`SNAPSHOT_HASH_POLICY_PENDING`, and `SHARED_SCHEMA_PENDING`.

Dataset digests are format-validated producer claims, not verified authenticity
or a canonical frame hash; the adapter has no source bytes. No instrument registry
or symbol-to-assets mapping, cross-timeframe aggregation check, Quote/Book freshness
policy, cost approval, account balance, sizing, or reservation is supplied here.
Market owns normalization, datasets, and the proposal; Strategy owns only this
consumer projection and diagnostics. Shared schema/time/provenance/market-rule
agreement, CandidateSignal/AgentDecision contracts, complete risk authorization,
and execution integration remain pending. No CandidateSignal, TradeIntent, order,
agent/risk approval, external request, or live action is produced.

## Explicit MarketFrame v1 diagnostic consumer

The same `assess_market_frame(frame, pe=...)` entry point also accepts real
`wallex.market.frame_v1.MarketFrameV1` objects labelled `marketframe-v1`.
The Market import is lazy and used only for explicitly labelled v1 inputs;
the v0 passive interface, validation, reasons and missing-data behavior remain
unchanged. Trend, Pullback, Stop and Risk calculations are unchanged.

V1 is checked using Market's existing `verify_snapshot_hash`,
`build_market_frame_v1` and `canonical_frame_json` helpers. The supplied hash
must verify, and the supplied fields must match a fresh validated canonical
reconstruction, including identity, cutoff, histories, availability, aggregate
times, sources, policy, Quote/Depth, quality and draft status. A recomputed hash
alone cannot make inconsistent derived fields valid. Hash integrity does not
prove source authenticity or approval. No hashing/normalization policy is
reimplemented in Strategy.

This initial v1 consumer requires `QualityV1.COMPLETE` and no quality reasons.
Missing/stale receipt evidence, stale histories, unavailable/invalid Quote or
Depth, incomplete quality, mismatched identity/timeframe, or inconsistent/hash-
tampered snapshots reject the frame before any technical assessment. V1's
builder can produce INCOMPLETE diagnostic frames, but this consumer does not
admit them; the existing v0 diagnostic behavior stays intact. Freshness thresholds
are explicit producer-policy claims, not newly approved Strategy/live defaults.

`AvailableCandle.candle` is projected into existing Strategy candles without
float conversion, rounding, invented prices or dataset hashes. Each v1
`HistoryDiagnostics.dataset_sha256` is explicitly `None`: v1's canonical snapshot
hash is not a dataset-file digest. `FrameMetadata.v1_evidence` preserves a rebuilt
immutable v1 record with every per-candle receipt time/source, source list,
policy, nested Quote, Depth levels and quality evidence. Top-level metadata also
preserves actual event/received times, bid/ask, spread, depth validity and hash.
V0 metadata keeps `v1_evidence=None` and its original dataset digest claims.

The existing Risk pipeline consumes these diagnostics without new wiring or
changing any Risk module. Explicit Pe is never inferred from candle close or ask
price. Its source/freshness and cost/account evidence remain caller obligations;
only the unchanged local diagnostic conditions can yield theoretical sizing.

**COMPLETE is data quality only; `contract_status=DRAFT` stays unapproved.**
Adapter and pipeline outputs permanently retain `executable_action=NO_TRADE`
and `executable_ready=False`. No Gate activation, RiskDecision authorization,
TradeIntent, execution request, account selection or credit-account assumption
is introduced. Authentic provenance, approved shared schemas, instrument/cost
rules, account correlation and atomic admission remain unresolved.

```text
python -B -m unittest wallex.strategy.test_frame_adapter_v1 -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```

## Limitations and planned integration

The shared **MarketFrame contract is pending**. Market owners must confirm
timestamp semantics/units, UTC alignment, closed-bar flags, symbol/timeframe
identity, ordered OHLC history, freshness, and gap policy. Callers must supply
a single instrument/timeframe; this local record carries no symbol and cannot
detect mixed-instrument history. Continuous UTC bars are assumed; session or
exchange outages require an explicit future team policy.

The first-bar convention makes ATR dependent on the chosen history start;
historical resumption/state persistence is not implemented. Indicator minima
do not enforce the full 250-hour warm-up automatically. Final TP-1 signal
composition, entry confirmation, cost checks, and position sizing remain
deferred. Initial stop levels remain proposals pending those integrations. No risk, execution, audit, or shared contract
integration is wired by this task.

There are no market-data fetches, credentials, external API calls, background
workers, order submission, or live trading. Missing input never becomes a
synthetic market observation. Risk decisions and execution remain separate.

## Validation

Python 3.11 standard library only; no third-party dependencies. Run all unit
tests without generating bytecode:

```text
python -B -m unittest discover -s wallex -p "test_*.py" -v
```

Test candles are synthetic fixtures. References use hand-calculated seeds,
recurrence values, and gap examples, with prefix-stability and cutoff tests.
