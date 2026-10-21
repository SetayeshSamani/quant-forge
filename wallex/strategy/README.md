# Strategy foundation

Purpose: prepare the deterministic TP-1 Trend Pullback interface. Final
indicators, pullback rules, confirmation logic, and entry calculations are
intentionally deferred. Every evaluation returns `NO_TRADE`.

Ownership: Setayesh owns this directory, `wallex/risk/`, and `wallex/infra/`
only. This module does not define or modify shared contracts, market data,
backtesting, shadow trading, execution, or audit modules.

Provisional interfaces: frozen `StrategyInput(symbol, close_prices)` uses
a tuple of positive, finite `Decimal` closing prices supplied by the caller.
`evaluate(inputs=None)` returns a frozen `StrategyDecision(signal, reason)`.
Missing values give `MISSING_DATA`; malformed inputs give `INVALID_INPUT`;
valid placeholder inputs give `CALCULATIONS_NOT_IMPLEMENTED`. These are
internal structures, not team-approved cross-module contracts.

Dependencies awaiting teammates: the approved TP-1 specification, instrument
and timeframe definitions, candle history and freshness rules from the market
module, and shared signal contracts. The team Project Plan is not available
in this checkout; this scaffold makes no claims beyond the Task 01 scope.

Safety limitations: no fabricated prices, confirmed signals, final strategy
calculations, data fetching, credentials, exchange calls, or order submission.
Price-shape validation does not establish freshness or data sufficiency.
There is no path that emits an entry signal.

Planned integration: adapt team market contracts into this internal input;
implement TP-1 only after approval of the specification; pass future strategy
intent to the risk gate, then to teammate-owned execution/audit adapters.
Backtest and shadow modules can evaluate the same deterministic interface.
No integrations are wired by this foundation.

Dependencies: Python 3.11 standard library only; no third-party packages.
From the repository root, test without bytecode files:

```text
python -B -m unittest discover -s wallex -p test_engine.py -v
```

Test prices are explicitly synthetic fixtures, never production market data.
