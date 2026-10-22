# Risk calculations and provisional gate

Purpose: pure theoretical position sizing and a separate minimal fail-closed
quantity/notional gate. Neither calculation validity nor a gate decision is an
order or complete risk authorization. Full portfolio/account risk authorization
remains future work.

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

Gate safety limitations: this is only a quantity/notional check. It does not
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

## Pure theoretical position sizing

Import `SizingInputs`, `assess_position_size`, `RiskProfile`, and `policy_for`
from `wallex.risk.sizing`. `assess_position_size(inputs=None)` accepts only the
local frozen `SizingInputs` record and returns a frozen `SizingAssessment`.
These are provisional internal interfaces, not shared account/intent contracts.
Missing fields default to `None` only to represent missing evidence; they never
activate a policy or invent balances, costs, or prices.

| Input | Explicit units and validation |
| --- | --- |
| `equity` | Positive finite Decimal account equity in quote-currency units. |
| `free_quote` | Nonnegative finite Decimal available quote funds; zero rejects sizing. |
| `quote_asset` | Explicit nonempty case-preserved identifier, at most 200 characters, without whitespace/control characters. No registry lookup or IRR/IRT conversion is inferred. |
| `pe` | Positive finite Decimal **maximum permitted BUY price**, in quote units per base unit; never inferred from candle close or Quote. |
| `initial_stop` | Positive finite Decimal proposed stop, in the same quote/base units; must be strictly below Pe. |
| `worst_case_extra_loss_per_unit` | Explicit nonnegative finite Decimal additional worst-case quote loss per base unit. Missing evidence rejects; zero must be supplied explicitly. |
| `risk_fraction`, `max_position_fraction` | Explicit finite Decimal fractions in `(0, 1]`, not percentage-unit values. No strings, integers, booleans, floats, coercion, or clamping. |

All numeric fields are validated as Decimals. Every input Decimal is bounded to
at most **100 coefficient digits**, with its stored decimal exponent in
**[-200, 200]**, including zero values. Out-of-domain representations reject as
`NUMERIC_RANGE_EXCEEDED` without normalization or rounding. These are local
arithmetic/resource bounds, not instrument tick/lot rules.

A positive numeric cost estimate is still only a producer claim; even an explicit
zero is not proof that fees/slippage were verified. Costs are additional to the
Pe-to-stop loss. All price, cost, equity and free-fund units must agree upstream;
a matching quote label alone cannot verify denomination or source identity.

```text
unit_loss = (Pe - initial_stop) + worst_case_extra_loss_per_unit
risk_budget = equity * risk_fraction
position_budget = equity * max_position_fraction
notional_budget = min(position_budget, free_quote)
quantity_by_risk = risk_budget / unit_loss
quantity_by_notional = notional_budget / Pe
theoretical_quantity_upper_bound = min(quantity_by_risk, quantity_by_notional)
```

Unit loss is quote/base, budgets are quote, and the resulting quantity is base.
The free-funds constraint caps **gross Pe notional only**. The extra-loss input is
a loss allowance, not an independently validated cash/fee reservation. Upfront
fees and fee-asset availability still require separate checks before any order.

Budget products and unit-loss addition/subtraction run in a private 512-digit
Decimal context, exponent bounds [-999, 999], with inexact/rounded, overflow,
underflow, subnormal, clamp, invalid-operation and division-by-zero traps. The
input bounds need at most 501 digits for aligned price sums and at most 200 for
products; uncertain or unrepresentable arithmetic rejects instead of silently
changing the budgets or loss model.

Each positive quotient is computed separately with **34 significant digits and
ROUND_FLOOR** in another private context. Nonterminating division is deliberately
rounded **down**, never up. This is a conservative numerical cap, not exchange
step-size rounding or an executable order quantity. No quantity increments,
ticks, minimum notionals, or fee assumptions are selected.

After division, `Fraction(Decimal)` reconstructs the exact input rational values,
independently of every Decimal context. The module verifies exact equality of
all calculated budgets/unit loss to their input formulas and then requires:

```text
quantity > 0
quantity * exact_unit_loss <= exact_equity * exact_risk_fraction
quantity * Pe <= min(exact_equity * exact_max_position_fraction, exact_free_quote)
```

Any identity, inequality, arithmetic or positivity failure rejects the entire
calculation. Caller precision, rounding, exponent bounds, traps and flags do not
alter the result or get changed by this module.

On success the assessment contains `calculation_valid=True`, the immutable input
record, `unit_loss`, `risk_budget`, `position_budget`, `notional_budget`, and
`theoretical_quantity_upper_bound`. Its reasons include `CALCULATION_VALID`,
`PROVISIONAL_COST_EVIDENCE` and `NOT_TRADING_AUTHORIZATION`. Every failure has
`calculation_valid=False`, an explicit reason and rejection field, and **all
input/calculation fields None**, including quantity. Every result has permanently
`executable_ready=False`; it cannot be enabled through constructor arguments.
No approved quantity or trading authorization is published.

Missing/invalid inputs, asset identity, policy or costs; stop >= Pe; fractions
outside `(0, 1]`; zero buying capacity; numeric bounds; arithmetic failures;
precision mismatches; budget violations; and nonpositive quantities all fail
closed. Fault-injection tests verify the independent arithmetic/inequality
checks also reject unsafe context changes rather than publish usable quantities.

## Explicit TP-1 policy configuration

`policy_for(RiskProfile.L0)` and `policy_for(RiskProfile.R0)` return frozen policy
records. The helper requires an explicit Enum selection, has no default, and does
not activate global configuration. The caller must explicitly copy the returned
fractions into its inputs, or supply explicit fractions directly.

| Profile | Per-trade risk fraction | Maximum position-notional fraction |
| --- | --- | --- |
| L0 | 0.001 = 0.10% | 0.20 = 20% |
| R0 | 0.0025 = 0.25% | 0.30 = 30% |

Synthetic independent check: equity/free funds 10000 quote, Pe 100, stop 95,
and explicitly supplied additional loss 0 yield unit loss 5. L0 has risk budget
10, position budget 2000, and theoretical cap **2 base units**. R0 has risk budget
25, position budget 3000, and theoretical cap **5 base units**. These examples
are not real account evidence, policy activation, or recommendations to trade.
Daily-loss and total-drawdown limits are not part of these profile calculations.

## Integration and full risk authorization still pending

Pure sizing does not call `evaluate_entry`, create an `EntryRequest`, issue a
`RiskDecision`, build a TradeIntent/order, approve an Agent, or invoke execution.
The existing `gate.py` behavior and exports are unchanged. Its `allowed=True`
remains a separate partial quantity/notional check, never complete authorization.
Sizing imports no Market, Execution, Audit, Strategy or shared-contract modules;
there are no files, environment configuration, credentials, network requests,
background tasks, or live-order paths in this calculation.

Production integration still needs actual market/Quote freshness; approved Pe
provenance; instrument lot/tick sizes; minimum/maximum quantities and minimum
notional; validated fees/slippage and fee funding; existing positions/open orders;
portfolio exposures and reservations; daily-loss and total-drawdown limits;
exchange account availability; and explicit live-trading authorization.
One-position-only rules require account/portfolio state and are **not enforced**.
Stop execution/fill outcomes and worst-case estimates are not guaranteed by these
formulas. Shared units, evidence/version references and full risk-guard contracts
must be agreed with their owners before any executable integration.

Run the complete suite without generating bytecode:

```text
python -B -m unittest discover -s wallex -p "test_*.py" -v
```
