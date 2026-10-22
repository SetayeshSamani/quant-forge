# Durable ledger and replay reconciliation handoff

## Sources, scope and ancestry

Repository: `SetayeshSamani/quant-forge`, origin
`https://github.com/SetayeshSamani/quant-forge.git`.
Original fetched main/default development base: `de3051be409e2dfb3530e556cd6205f201b15578`.
Main was established from the existing origin/main and updated with
`git pull --ff-only`; no artificial main commit was created. This initially
unborn local checkout had no previous execution implementation or unpublished
commits. Work already produced in this task was inspected and extended after the
updated prompt; no second execution implementation was introduced. No applicable
AGENTS.md or personal local branch requiring a rename was found.

| Topic branch | Actual base/dependency | Review scope |
| --- | --- | --- |
| `feat/audit-intent-ledger` | main `de3051be409e2dfb3530e556cd6205f201b15578` | Provisional consumers, durable admission, writer/recovery controls; tip `8814b4ea230d314cc3f5f6d442ada7a341ac1a18` |
| `feat/execution-replay-reconciliation` | `feat/audit-intent-ledger` at `8814b4ea230d314cc3f5f6d442ada7a341ac1a18` | Replay gateway, observation/fill accounting, recovery hardening, runnable example |

Before publication a fresh fetch found that main had advanced to
`dd3b40cf42b5abd58a318551bd05f93bca966205`. PRs 2 and 3 were merged by the
teammate during this work. Main is updated by fast-forward; the topic branches
retain their original ancestry without reset, rebase or history rewriting. A
detached temporary worktree at this newer main, with only the two owned module
directories copied in, passes all **140 tests**. The newer indicator/trend code
does not change the Strategy engine exports or Risk/Runtime consumers used here.

The first three commits are `a7716b4`, `233613a`, `8814b4e`; replay behavior
commits are `af66837`, `95403fc`, `2d9361d`, followed by this example/handoff
commit. PR 2 (`feat/wallex-tp1-indicators` -> main), inspected at
`e9874078a205147bd14803b16864deb8d17103a0`, and PR 3
(`feat/wallex-tp1-trend-filter` -> main) at
`b2db2f312bbf069506707abfc308d0b07501eb3e` were not modified or merged by this
task. No teammate history is merged into these topics. Only the two owned topic
branches are to be pushed, with draft PR 1 targeting main and draft PR 2 targeting
the ledger branch. No feature is merged into main by this stage.

The complete 236-line agreed design direction was read from
`C:\Users\FATIMA\Downloads\QuantForge_Project_Plan.md`; SHA-256 is
`56a2c3d370ab2fffe6908139265334eea21d76f4faaea3efe9f56a93017ac7df`.
The user confirmed its design direction despite its older proposal wording.
It supplies neither live-trading authorization nor missing account settings.
All code, tests, example and documentation changes stay inside
`wallex/execution/**` and `wallex/audit/**`.

## Implemented consumer interfaces

`ExecutionRequest` combines a typed `TradeIntent`, the actual
`wallex.risk.RiskDecision`, `DecisionReferences`, supplied `Reservation` and
explicit evidence kind. Intent identity/client identity come from the producer
and are immutable. The entire canonical request, including decision/version
references, explicit capacity and reservation, is SHA-256 fingerprinted. Decimal
monetary values serialize as exact strings; timestamps are aware UTC. No size,
profile, market, fee, tick/step or deadline is picked by the consumer.

`Ledger(path).plan(request, at=...)` atomically persists PLANNED, identity,
fingerprint, mode/account/instrument association, complete request, reservation
and initial audit. Identical requests return the same record; conflicting reuse
of intent ID or `(account_id, client_id)` is refused. Capacity checks aggregate
held reservations with exact Decimal arithmetic inside the same transaction.
`get`, `records`, `reserved`, `events`, `fills` and `receipt` return persisted
views. Every mutation has an atomic audit; a failed audit rolls it back.

`ReplayGateway(ledger, ReplayTransport(...), clock=...)` exposes:

- `submit(request)`: replay entry submission, existing identity lookup without
  resubmission, shadow decision audit, or explicit live refusal.
- `reconcile(intent_id)`: lookup the original account/client identity; no submit.
- `recover_account(account_id, run_id=..., trace_id=...)`: reconcile persisted
  orders before allowing any new execution after gateway restart. Its scope is
  explicitly `synthetic_order_only`, never account balance reconciliation.
- `cancel(intent_id)`: synthetic cancellation after durable CANCEL_PENDING.
- `consume_strategy_decision(actual_decision, account_id=..., run_id=...,
  trace_id=...)`: audit the actual Strategy NO_TRADE result without an order.
- `stop_new_entries`: blocks entry only. Reconciliation and cancellation remain
  usable; risk-reducing exits require a later implementation and review.

The gateway admits only replay BUY LIMIT entries for now. SELL/exit execution is
refused, even though the provisional identity/units model can describe it. Shadow
is audit-only and is not Roham's Shadow Runner. Live has no transport adapter and
raises `LiveUnavailable`. There are no REST/MCP clients, credentials or Agent order
tools. Fixture entries are explicitly synthetic, not Strategy-produced signals.

`ExecutionReceipt` preserves mode/run/trace/version references, fingerprint,
order/source identity, cumulative/remaining quantities, all deduplicated fills,
last validated source evidence, original reservation and uncertainty reasons.
UNKNOWN/control states expose current cumulative and remaining quantities as null;
last known evidence remains separately available. `Fill` preserves source fill ID,
base units, price in quote/base, fee amount/asset, received/source time and reasons
for unknown fee/time. Base-asset fees reduce known net acquired base. Sellable
quantity is always unknown because account free balance is not reconciled.
`AccountSnapshot` remains separate and only represents unavailable account data;
known balances are refused until that producer contract is agreed.

## State and persistence rules

| Trigger | Durable state transition / outcome |
| --- | --- |
| Validated new replay request | no record -> PLANNED + held reservation + initial audit |
| First synthetic submit | PLANNED -> SUBMITTING committed before I/O |
| Accepted usable observation | SUBMITTING -> WORKING / PARTIAL / FILLED / CANCELED / REJECTED as supported by exact evidence |
| Lost response or unusable observation | SUBMITTING -> UNKNOWN; no automatic rejection/retry |
| Lookup original identity | persisted state -> RECONCILING -> validated observed state or UNKNOWN |
| Lookup timeout or not found | RECONCILING -> UNKNOWN; reservation retained, no replacement client ID |
| Cancellation request | WORKING/PARTIAL -> CANCEL_PENDING -> validated observed state or UNKNOWN |
| Proven unsent expired request | PLANNED -> REJECTED; reservation may be released because transport never began |
| Storage/audit error | raise; retain last committed state for later recovery, never repeat I/O to repair a failed commit |

Only explicit control transitions and validated receipt transitions exist. Exact
integer record versions guard updates. FILLED cannot reopen; CANCELED may receive
later cumulative cancellation evidence or become FILLED after a race. REJECTED
cannot turn into another observed state. Cumulative fills/source observation time
cannot regress, source-order identity cannot change, fills cannot exceed order
quantity, and cumulative quantity must match all persisted deduplicated fills.
An incomplete source fill list becomes UNKNOWN instead of fabricated holdings.
Repeated source fill IDs with identical economic content are idempotent even with
a new delivery time; conflicting values or reassignment to another intent fail.
Fill insertion, state and audit are a single SQLite transaction.

Both cancel-then-fill and partial-fill-then-cancel fixtures are covered. CANCELED
can retain nonzero fills and does not prove a flat account. All external terminal
receipts retain reservations conservatively in this stage. There is no account
settlement/reservation release based on a venue receipt. Held exposure blocks any
new entry for that account, including after successful order-only recovery.

Each account writer holds a nonblocking OS file lock across synthetic I/O, outside
short SQLite transactions. Distinct gateway processes on the same local canonical
database path cannot concurrently own its account writer. Kernel locks release on
process death while SUBMITTING/UNKNOWN survives. This is single-host coordination,
not distributed exactly-once execution. All writers must follow the same protocol;
network filesystems, hard-link aliases and multiple independent ledger copies are
unsupported. SQLite is trusted local storage, not a tamper-proof audit service.
Request fingerprint/identity inconsistency on disk stops recovery before lookup.

## Verification and runnable scenario

Verified locally on Windows, Python 3.14.8, standard library only. From repository
root, the full suite passes **68 tests**, including all **25 unchanged** existing
strategy/risk/runtime tests:

```powershell
python -B -m unittest discover -s wallex -p 'test_*.py' -v
python -B -m unittest discover -s wallex -p 'test_engine.py' -v
python -B -m unittest discover -s wallex -p 'test_gate.py' -v
python -B -m unittest discover -s wallex -p 'test_runtime.py' -v
python -B -m wallex.execution.example
git diff --check
```

The three individual documented commands above pass 5 strategy, 12 risk and
8 runtime cases respectively. The example succeeds with one submit and one
lookup. The full-suite command also passes 140 tests in the detached newer-main
compatibility worktree, without changing either topic's ancestry. The baseline
ownership diff command is:

```powershell
git diff --exit-code de3051be409e2dfb3530e556cd6205f201b15578 -- wallex/strategy wallex/risk wallex/infra
git diff --name-only main...HEAD
```

The first verifies those producer modules are unchanged from the actual topic
base; the second verifies the proposed PR changes stay in the two owned paths.
Tests use temporary databases, deterministic clocks and supplied synthetic events.
They exercise persist-before-I/O without an open SQLite transaction, idempotency
and collisions, competing instances/processes, aggregated reservations, lost
acceptance responses, not-found uncertainty, hard process exit after acceptance,
restart recovery, repeated/conflicting fills, partial/cancel races, unknown fees,
atomic audit failures, corrupt request binding, NO_TRADE, shadow and live refusal.
The full uncertainty flow is additionally run with socket/urlopen entry points
forbidden. The POSIX writer path, other Python versions, exchange APIs and real
account reconciliation were not verified. No root linter/build/CI configuration
or additional dependency environment was introduced.

The runnable example uses a temporary database and an explicitly labeled
synthetic fixture: persist -> synthetic acceptance/response loss -> UNKNOWN ->
gateway restart -> new entry blocked -> lookup same client ID -> WORKING.
Expected JSON includes `transport_submit_count: 1`, `transport_lookup_count: 1`,
`held_reservation_quote: "21"`, `account_balances_reconciled: false`, and null
sellable quantity. Its fixed quantity/price/capacity and 20-second expiry are
fixture values, not production risk or timing defaults. The in-memory synthetic
venue fixture is reused across the example's gateway restart; the separate hard
process-death test injects a saved synthetic evidence tape explicitly.

## Required agreement before broader integration

| Producer/owner | Actual gap and requested contract |
| --- | --- |
| Setayesh: Strategy/Risk/Agent | Current Strategy emits NO_TRADE; current RiskDecision has only allowed/reason from quantity/notional checks. It has no sized TradeIntent, explicit reservation, account snapshot, stable decision ID or policy/schema/version provenance. Agree ownership and stable lifetime of intent/client IDs, risk-sized quantity/reserve/capacity, account/instrument/assets, expiry, decision/version refs and explicit missing-Agent reasons. `allowed=True` remains a partial check, never live authorization. |
| Roham: replay/market orchestration | Agree account/client/source-order correlation, stable source fill IDs, exact units, price, fee asset/amount and source/received UTC times. Supply cumulative fill details sufficient to resolve every claimed fill; a cumulative number alone is insufficient. Current injected fixture is an execution dependency, not a matching engine, market collector, backtester or Shadow Runner. |
| Shared schemas | No canonical shared definitions were available in the inspected histories. Reconcile provisional TradeIntent, ExecutionRequest, ExecutionReceipt, Fill, AuditEvent and AccountSnapshot field/version/null semantics before registering any shared schema. Do not copy these into teammates' modules as presumed agreement. |
| Account/runtime and future live stage | Supply authoritative balances/free/locked funds, open orders/positions and settlement semantics before releasing terminal reservations or claiming sellability/flatness. Resolve partial-fill correction/bust rules, dust, fee sufficiency, tick/step/minimums, account risk settings, recovery/operator workflow and single-writer deployment. No L0/R0, markets or live operational values were selected here. Authentication, durable producer authorization, real exchange lookup/API adapter and reviewed execution remain future dependencies. |

No producer/shared files were changed to fill these gaps. Request/version
references are declared provenance; they are not independently verified
authorization or a substitute for those producer contracts.

## پیام هماهنگی برای ستایش و رهام

ستایش: execution فعلاً Intent مصنوعی با شناسهٔ ثابت، مقدار و قیمت Decimal،
رزرو صریح و ارجاع تصمیم/نسخه را همراه RiskDecision واقعی فعلی مصرف می‌کند؛
allowed فقط همان بررسی محدود موجود است. NO_TRADE سفارش تولید نمی‌کند.
مالکیت شناسه‌ها، خروجی sizing و رزرو و قرارداد نسخه‌دار تصمیم/حساب هنوز باید
توافق شود.

رهام: transport تزریق‌شده فقط fixture اجرای replay است. خروجی آن receipt،
fillهای deduplicate‌شده، وضعیت عدم قطعیت و audit پایدار است. برای اتصال runner،
شناسهٔ source/fill پایدار و جزئیات کامل مقدار، قیمت، کارمزد و زمان لازم است.
پاسخ گم‌شده UNKNOWN می‌شود؛ بازیابی همان client_id را lookup می‌کند و سفارش
دیگری نمی‌فرستد. لغو با fill غیرصفر حفظ می‌شود. موجودی قابل فروش، تسویه و
reconciliation واقعی حساب پیاده نشده و live همچنان غیرفعال است.
