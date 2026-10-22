# Risk calculations and provisional gate

Purpose: pure theoretical position sizing, local daily-loss/drawdown checks,
and a separate minimal fail-closed quantity/notional gate. Calculation validity,
passing local limits, and a gate decision are not orders or complete risk
authorization. Full portfolio/account risk authorization remains future work.

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
portfolio exposures and reservations; persistent account evidence/reset wiring
for the local loss guards below; exchange account availability; and explicit
live-trading authorization.
One-position-only rules require account/portfolio state and are **not enforced**.
Stop execution/fill outcomes and worst-case estimates are not guaranteed by these
formulas. Shared units, evidence/version references and full risk-guard contracts
must be agreed with their owners before any executable integration.

Run the complete suite without generating bytecode:

```text
python -B -m unittest discover -s wallex -p "test_*.py" -v
```

## Local daily loss and total drawdown guards

`wallex.risk.loss_guards` provides frozen `LossGuardPolicy`, `LossGuardState`,
and `LossGuardAssessment` records. Explicitly select
`loss_policy_for(RiskProfile.L0)` or `loss_policy_for(RiskProfile.R0)` using the
existing enum from `wallex.risk.sizing`. No profile is selected or activated by
default. Only these agreed profile/fraction combinations are accepted, including
when a caller directly constructs a policy; changed or malformed thresholds reject.
The guard never calls sizing or `evaluate_entry`.

| Profile | Daily loss fraction | Maximum drawdown fraction |
| --- | --- | --- |
| L0 | 0.005 = 0.50% | 0.01 = 1% |
| R0 | 0.01 = 1% | 0.02 = 2% |

Pass a `LossGuardState` explicitly to `assess_loss_guards(state=None)`. Every
field is required evidence; its `None` default represents absence, not a balance,
clock value, baseline, policy, or assumed False flag:

| State field | Meaning and validation |
| --- | --- |
| `current_equity` | Positive finite Decimal account equity at the supplied observation time. |
| `day_start_equity` | Positive finite Decimal equity at the start of the decision's UTC trading day. |
| `high_water_equity` | Positive finite Decimal high-water equity supplied from the caller's verified history; must be at least both current and day-start equity. Never inferred or automatically updated here. |
| `quote_asset` | Explicit common quote-unit identifier for all three valuations, preserving case; nonempty, at most 200 characters, without whitespace/control characters. |
| `as_of` | Explicit nonnegative integer UTC Unix-second decision cutoff, excluding bool. |
| `day_start_at` | UTC midnight containing as_of: `(as_of // 86400) * 86400`. |
| `equity_observed_at` | Nonnegative integer UTC Unix seconds, required to satisfy `day_start_at <= equity_observed_at <= as_of`. A prior-day observation cannot stand in for current-day equity. |
| `policy` | Explicit supported `LossGuardPolicy`; missing or mismatched policy evidence rejects. |
| `prior_daily_breach`, `prior_drawdown_breach` | Required actual bools from historical state. Missing evidence fails closed; integers, Decimal zero, strings, and other truthy/falsy objects are not accepted. |

Valuations must use the same explicitly declared quote currency and consistent
account scope. The quote label alone does not verify their denominations or
source identity; no conversion, account collection, or valuation is performed.
**Proposed account equity includes unrealized PnL only if the future account data
source provides a consistent mark-to-market valuation.** That source is not
verified or implemented here. Deposits, withdrawals and other external cash flows
need an agreed upstream baseline/valuation policy; these formulas do not isolate
realized trading PnL or repair inconsistent account history.

Daily equity loss and peak-to-current drawdown are different metrics:

```text
daily_loss = max(0, day_start_equity - current_equity)
daily_loss_budget = day_start_equity * daily_loss_fraction
current_drawdown = max(0, high_water_equity - current_equity)
drawdown_budget = high_water_equity * max_drawdown_fraction
```

All four outputs are quote-unit amounts. Daily loss compares against the current
UTC day's starting equity. Drawdown compares against the supplied historical
high-water equity, which need not be today's start. A day can show a gain and
still be below its historical peak; a gain never creates a negative daily loss.

The guard blocks new entries when **any** condition is true:

```text
daily_loss >= daily_loss_budget
current_drawdown >= drawdown_budget
prior_daily_breach is True
prior_drawdown_breach is True
```

**Equality blocks entry.** Synthetic starting/high-water equity 10000 quote gives
L0 budgets 50 daily / 100 drawdown, and R0 budgets 100 / 200. Under L0, current
equity 9950 with day start/high water 10000 reaches the daily limit. For drawdown
alone, current/day-start equity 9900 and high water 10000 reaches the 100 limit
with daily loss zero. Under R0, the analogous equality examples are current 9900
for daily loss, or current/day-start 9800 for drawdown. These are synthetic
references, not actual account evidence or live policy activation.

Arithmetic reuses the sizing module's input representation bounds: at most 100
coefficient digits and stored Decimal exponent [-200,200]. Equity differences and
budget products use a private 512-digit Decimal context with fixed exponent bounds
[-999,999] and inexact/rounded/overflow/underflow/subnormal/clamp/invalid-operation
traps. It does not inherit or change caller settings. Exact `Fraction(Decimal)`
identities independently validate all four amounts; breach comparisons use those
exact values, with no division, floating-point conversion, or rounding at a limit.
Any arithmetic or precision failure blocks entries without usable local evidence.

For valid input, the result contains `calculation_valid=True`, the frozen supplied
state, all four exact amounts, and stable reason codes. `blocks_new_entries=False`
means only **these local loss-limit checks** found no blocking condition; it is
not complete portfolio risk permission. `WITHIN_LOCAL_LIMITS` describes that narrow
case. Breaches retain evidence and report `DAILY_LOSS_LIMIT_REACHED`,
`DRAWDOWN_LIMIT_REACHED`, `PREVIOUS_DAILY_BREACH`, and/or
`PREVIOUS_DRAWDOWN_BREACH` in that order.

Malformed or missing state has `calculation_valid=False`,
`blocks_new_entries=True`, an explicit reason/rejection field, and no state,
amounts, or latched-flag evidence in the result. Relevant failures include missing
history/flags/policy, invalid types/equity/identity, inconsistent high-water or UTC
baselines, numeric range, arithmetic, and precision errors. Every assessment has
permanently `executable_ready=False`, even when within local limits.

## Breach latches, persistence and authorization boundaries

`daily_breach_latched` is the current daily breach OR `prior_daily_breach`;
`drawdown_breach_latched` is the current drawdown breach OR its prior flag. A
previous breach remains blocking after equity recovery. These are returned latch
proposals for the future persistent account adapter; the source state is never
mutated and nothing is persisted by this pure module. Repeated calls are
deterministic, and no hidden in-memory history or automatic reset exists.

Changing UTC days does **not** clear a True prior daily flag. An eventual daily
reset must be tied to verified new-day state and an agreed persistent reset
policy, not wall clock, a new function call, or a runtime transition. The account
adapter must durably retain/reload high-water marks, day identity/baselines, and
breach flags across runtime restarts before supplying new-entry evidence.

Timestamp ordering is not proof of source authenticity or live freshness. No
maximum observation age within the day, external-source verification, account
availability, or automatic mark-to-market computation is implemented. Passing
local limits does not enforce positions, pending orders, concurrent reservations,
future trade-loss commitments, account valuation correctness, live evidence
freshness, or exchange trading rules. It does not issue a quantity, CandidateSignal,
TradeIntent, Agent approval, full risk authorization, runtime activation, or order.
Sizing, the provisional gate, and these local loss guards remain separate pure
checks until explicitly integrated with the agreed portfolio/account contracts.
No teammate modules, shared contracts, or live API paths are changed.
