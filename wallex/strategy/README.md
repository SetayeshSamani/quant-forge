# TP-1 strategy and technical indicators

Purpose: deterministic EMA, Wilder ATR, H1 trend eligibility, and M15
pullback eligibility for the TP-1 Cost-Aware Trend Pullback strategy. The foundation engine remains a
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
composition, entry confirmation, cost checks, stop-loss calculations, and
position sizing remain deferred. No risk, execution, audit, or shared contract
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
