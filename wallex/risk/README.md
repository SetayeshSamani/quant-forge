# Risk calculations and provisional gate

Purpose: pure theoretical sizing, local loss limits, read-only portfolio entry
checks, and a separate provisional quantity/notional gate. Passing local checks
does not grant complete risk or execution authorization. Verified account
integration and atomic admission remain future work.

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
Sizing does not enforce one-position-only rules; the portfolio guard below
checks supplied snapshots without making admission atomic.
Stop execution/fill outcomes and worst-case estimates are not guaranteed by these
formulas. Shared units, evidence/version references and full risk-guard contracts
must be agreed with their owners before any executable integration.

Run the complete suite without generating bytecode:

```text
python -B -m unittest discover -s wallex -p "test_*.py" -q
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

## Read-only portfolio entry guard

`wallex.risk.portfolio_guards.assess_portfolio_entry(state=None)` accepts a frozen
local `PortfolioGuardState` and returns a frozen `PortfolioGuardAssessment` with
immutable `PortfolioGuardReason` codes. This is a provisional consumer boundary,
not a shared schema or a connected/verified account adapter.

TP-1 permits at most **one active spot position across the relevant trading
account**. For a new BUY, any existing position blocks entry; multiple positions
also report `MULTIPLE_POSITIONS_DETECTED`. Checks cover the whole account, not
only the proposed symbol or selected quote asset. Required explicit fields are:

| State fields | Required meaning |
| --- | --- |
| `account_id`, `quote_asset` | Exact nonblank identifiers, at most 200 characters, without control characters or surrounding whitespace; quote asset permits no whitespace. Account IDs preserve internal spaces consistently with the inspected Execution identifier contract. |
| `as_of`, `snapshot_observed_at` | Nonnegative integer UTC Unix seconds, excluding bool. No wall clock is used. |
| `max_snapshot_age_seconds` | Nonnegative integer seconds, explicitly supplied by the caller, with no activated/approved global default. |
| `open_position_count` | Account-wide active spot positions. |
| `outstanding_order_count` | All outstanding orders, including pending BUY and SELL. |
| `uncertain_order_count` | UNKNOWN or otherwise unreconciled orders. |
| `held_reservation_count` | All held account reservations, independently of outstanding orders or apparent terminal receipts. |
| `unsettled_fill_count` | Unresolved fill/position/settlement uncertainty. |
| `positions_complete`, `orders_complete`, `reservations_complete`, `settlement_complete`, `account_scope_complete` | All required actual bool completeness claims, each explicitly True to proceed. |

Counts must be actual nonnegative integers, excluding bool; they may overlap and
are not inferred from one another. Every field defaults to `None` only to represent
missing evidence. Missing counts never mean zero and missing flags never mean True.
The quote label does not filter out positions, orders or reservations in other
assets within the relevant account scope. The future account adapter must supply
coherent complete aggregates; this function cannot verify their source authenticity.

Age is calculated exactly with integers and must satisfy:

```text
0 <= as_of - snapshot_observed_at <= max_snapshot_age_seconds
```

An observation after the cutoff reports `FUTURE_ACCOUNT_SNAPSHOT`; age above the
caller-supplied limit reports `STALE_ACCOUNT_SNAPSHOT`. Equality is accepted. An
explicit zero age limit requires observation exactly at the supplied cutoff,
not an inferred current wall-clock time. Validating this provisional age policy
does not establish its approval or actual source freshness.

After complete/fresh snapshot validation, **any positive count blocks new BUY
entries**, with distinct reasons for positions, outstanding orders, uncertain
orders, held reservations, and unsettled fills. A valid blocked snapshot retains
its evidence. Invalid, missing, incomplete, future, or stale evidence returns
`snapshot_valid=False`, `blocks_new_entries=True`, no state/age evidence, and a
reason plus rejected field. A complete flat snapshot reports
`NO_LOCAL_PORTFOLIO_BLOCKER` and `blocks_new_entries=False`; `snapshot_valid=True`
means local snapshot checks only. **Every result has fixed
`executable_ready=False`.** No quantity, approval, TradeIntent, order or admission
token is produced; no gate/sizing/loss guard or runtime action is called.

## Audit/Execution and concurrency boundary

Read-only review of draft PR #4 (`feat/audit-intent-ledger`) and its dependent
PR #5 (`feat/execution-replay-reconciliation`) confirms durable intent/client
identities, held capacity reservations, account writers and uncertainty recovery.
The future aggregate producer must include `PLANNED`, `SUBMITTING`, `WORKING`,
`PARTIAL` and `CANCEL_PENDING` orders and conservatively represent `UNKNOWN` and
`RECONCILING` uncertainty. UNKNOWN never means canceled/rejected/unfilled. Apparent
terminal status does not prove a flat/settled account or release a held reservation.
The guard does not implement an order-state mapper, lifecycle or reservation release.
Current Execution `AccountSnapshot` and order-only recovery do not establish
verified balances or complete portfolio coverage; explicit consumer mapping remains
pending. Its aware UTC datetime evidence also needs an agreed exact conversion
policy for this local integer-second interface.

Two callers can both pass the **same immutable flat snapshot**: the assessment
neither consumes capacity nor records an entry. It cannot eliminate concurrent
duplicate admission/submission. A passing local result must be revalidated inside
the agreed authoritative admission protocol using teammate-owned durable Audit
`Ledger.plan`, stable intent/client identities, capacity reservations and the
single-writer protocol. The existing ledger is not claimed to enforce this new
portfolio predicate automatically. No parallel ledger, database, lock, lease,
client-ID generator or order lifecycle is added here.

This guard applies only to **new entries**. Position closing, emergency exits,
cancellation and reconciliation are not new BUY attempts and must not be routed
through it as exit permission. No exit-blocking behavior is implemented. Full
account/risk authorization, atomic source/ledger revalidation, reservations,
verified evidence and live execution remain separate pending integration tasks.
PRs #4/#5, teammate modules and shared contracts are unchanged.

Run focused tests first, then the complete regression suite, without bytecode or
verbose passing logs:

```text
python -B -m unittest wallex.risk.test_portfolio_guards -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```

## Integrated Strategy/Risk diagnostic pipeline

`pipeline.py` exposes frozen `PipelineInputs` and `PipelineAssessment` records
and pure `assess_risk_pipeline(inputs=None)`. It composes the existing Strategy
frame adapter, initial-stop diagnostics, position sizer, loss guards and portfolio
guards without changing them. It performs no I/O, clock reads, persistence,
configuration lookup, account retrieval, order admission or execution. Existing
`gate.evaluate_entry` is not called: its quantity/notional checks are partial
validation and cannot establish full risk authorization or instrument compliance.

Supply every input explicitly:

| Input | Units and meaning |
| --- | --- |
| `frame` | Passive provisional MarketFrame v0 evidence accepted by Strategy's existing attribute interface; no mandatory Market import. |
| `pe` | Decimal quote/base maximum permitted BUY price Pe, never inferred from a candle close or claimed to be a fill price. |
| `worst_case_extra_loss_per_unit` | Decimal additional worst-case quote loss per base unit; missing evidence is rejected, including when zero would be convenient. Explicit zero is only a caller claim. |
| `equity`, `free_quote` | Decimal account values in the explicitly named quote asset, with no automatic currency conversion or balance defaults. |
| `quote_asset`, `account_id` | Explicit quote label and account identity; neither is inferred from another component. |
| `profile` | Actual `RiskProfile.L0` or `RiskProfile.R0`, explicitly selected; no default or automatic activation. |
| `loss_state`, `portfolio_state` | The existing immutable evidence records, including their own cutoff, completeness and policy fields. |

The frame must be a stable passive snapshot throughout the call. Freezing
`PipelineInputs` does not deep-freeze a foreign frame object. The adapter projects
validated candles into immutable diagnostics; `PipelineAssessment` retains those
results, not the mutable foreign frame. No shared schema is introduced here.

### Stage results, coherence and sizing

Frame/Trend/Pullback/optional explicit-Pe stop, loss guards and portfolio guards
are assessed independently. Their actual returned assessments and reason codes
are retained even if another component rejects its evidence. A component that
raises or returns the wrong result type is recorded in `failed_stages`, with
`COMPONENT_FAILURE`; its result remains `None`, without invented evidence or
arbitrary exception text. Later independent components still run. Invalid input
containers skip every stage with `INVALID_INPUT`.

Before sizing, the pipeline requires valid component evidence and compares:

- Frame metadata, loss state and portfolio state must share the same explicit
  `as_of`, in UTC Unix seconds. Candle timestamps retain Strategy's opening-time
  meaning; only completed, continuous M15/H1 histories available at that cutoff
  are accepted. The latest candle close can precede an off-grid cutoff.
- The explicit quote asset must match all three components exactly. No alias
  normalization, asset substitution or implicit conversion is performed.
- The explicit account must match the Portfolio state account.
- The selected L0/R0 profile must match the validated Loss policy. The unchanged
  sizer's `policy_for` supplies that profile's risk and position fractions.
- Explicit sizing equity must equal Loss state `current_equity`.

`coherence_valid` means these local input correlations passed, independently of
technical eligibility or breached guards. `local_conditions_satisfied` additionally
requires Trend AND Pullback eligibility, a valid explicit-Pe proposed stop,
nonblocking local loss/portfolio guards and a valid sizing calculation. Missing,
invalid or mismatched evidence blocks this local success. When prerequisites
fail, sizing is not called: `sizing=None` and `skipped_stages` identifies its
blocking reasons. If sizing itself rejects inputs, its actual failed assessment
is retained and contains no usable quantity. An attempted failed component is
distinguished from a skipped stage.

Only that complete local path produces the existing sizer's theoretical base-unit
quantity upper bound, nested in `assessment.sizing`; it is **not approved or
exchange-rounded**. Stop calculation is reused through Strategy's frame adapter,
not recomputed from candle close. Existing conservative Decimal calculations,
extra-loss budget treatment and fixed component precision remain unchanged.

### Permanent authorization and provenance limitations

Every result fixes `executable_ready=False` and `executable_action=NO_TRADE`, even
when local checks and sizing pass. `limitations` always records diagnostic-only
status, unapproved Market evidence, provisional Pe/cost provenance, unresolved
Loss/account provenance and unavailable atomic admission. No approved
CandidateSignal, RiskDecision, Agent decision, TradeIntent, order, execution
request or admission token is produced. No Gate or risk profile is activated.

MarketFrame v0 remains INCOMPLETE: Quote/Depth, event/receive time semantics,
canonical snapshot hash and approved shared schema are unresolved. Its dataset
hashes and source labels are claims, not verified provenance. Pe and cost source,
freshness, instrument rules, tick/lot rounding and upfront fee funding remain
unverified. A successful calculation does not repair those evidence gaps.

**LossGuardState has no independent `account_id`.** Matching quote, equity and
cutoff does not prove that its valuations, high-water history or breach flags
belong to the explicit Portfolio account. Nor does this interface prove that
supplied free funds come from that account or the same observation. Authentic
account correlation, valuation/freshness policy, persistent breach history and
restart recovery need agreed upstream contracts; no extra identity field or
shared contract is invented here.

Repeated assessments of one flat snapshot can both pass. Neither consumes
capacity nor reserves funds, so neither guarantees atomic admission. Authoritative
revalidation, stable intent identity, durable reservation and single-writer
coordination remain Audit/Execution ownership. This pipeline adds no parallel
ledger, lock or admission protocol and leaves their PRs #4/#5 unchanged.

Run the focused tests before the complete regression suite:

```text
python -B -m unittest wallex.risk.test_pipeline -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```
