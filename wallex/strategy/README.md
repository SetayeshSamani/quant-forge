# TP-1 strategy and technical indicators

Purpose: deterministic EMA and Wilder ATR calculations for the TP-1 Trend
Pullback strategy. The foundation engine remains a scaffold: every call to
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

## Limitations and planned integration

The shared **MarketFrame contract is pending**. Market owners must confirm
timestamp semantics/units, UTC alignment, closed-bar flags, symbol/timeframe
identity, ordered OHLC history, freshness, and gap policy. Callers must supply
a single instrument/timeframe; this local record carries no symbol and cannot
detect mixed-instrument history. Continuous UTC bars are assumed; session or
exchange outages require an explicit future team policy.

The first-bar convention makes ATR dependent on the chosen history start;
historical resumption/state persistence is not implemented. Indicator minima
do not enforce the full 250-hour warm-up automatically. Final TP-1 entry,
pullback, confirmation, and signal calculations remain deferred. No risk,
execution, audit, or shared contract integration is wired by this task.

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
