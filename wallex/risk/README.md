# Risk foundation

Purpose: a minimal fail-closed gate for new-entry candidates, with mandatory
positive quantity and notional limits. A risk decision is distinct from an order.

Ownership: Setayesh owns this directory, `wallex/strategy/`, and
`wallex/infra/` only. Shared contracts, market, backtest, shadow, execution,
and audit remain owned by teammates and are not changed here.

Provisional interfaces: frozen `EntryRequest(quantity, price)` and
`RiskLimits(max_quantity, max_notional)` require positive, finite `Decimal`
values. No coercion of floats, strings, integers, or booleans is performed.
`evaluate_entry(request=None, limits=None)` returns frozen
`RiskDecision(allowed, reason)`. Missing/invalid configuration, malformed
requests, arithmetic failures, or exceeded limits deny entry. Equality to
a limit is permitted. Multiplication uses enough precision for an exact
product and rejects inexact/underflow failures. No configuration is inferred
from environment variables or files.

Dependencies awaiting teammates: approved risk policy and numeric/unit
contracts; instrument precision and minimum order rules; balances, portfolio
exposure, outstanding orders, and current market/freshness information.
These internal dataclasses are not finalized shared contracts.

Safety limitations: this is only a quantity/notional check. It does not
implement stop-loss budgets, daily loss, leverage, exposure aggregation,
fees, liquidity, freshness, concurrency reservations, or authorization.
`allowed=True` means only these provisional checks passed. It must never
be treated as permission for live trading. No orders are constructed or
submitted, and no credentials or trading APIs are accessed.

Planned integration: adapt future strategy intent to a candidate, apply the
approved full risk policy using team-owned portfolio/market contracts, then
let teammate-owned execution and audit consume a separate decision.
Nothing invokes execution in this foundation. Runtime remains live-disabled.

Dependencies: Python 3.11 standard library only; no third-party packages.
From the repository root, test without bytecode files:

```text
python -B -m unittest discover -s wallex -p test_gate.py -v
```

All numeric examples in tests are synthetic fixtures.
