# Offline execution ledger and replay reconciliation

Scope: `wallex/execution/` and `wallex/audit/` only. The agreed design direction
was read in full from `QuantForge_Project_Plan.md` (236 lines), SHA-256
`56a2c3d370ab2fffe6908139265334eea21d76f4faaea3efe9f56a93017ac7df`.
Its design direction does not authorize live trading or select account settings.

The original development base is main
`de3051be409e2dfb3530e556cd6205f201b15578`. Fetch/prune/tags and main's tracked
fast-forward pull completed. Before publication, main advanced to
`dd3b40cf42b5abd58a318551bd05f93bca966205`; the new indicators/trend code was
also tested with this execution stage in a detached temporary worktree (140
passing tests). Topic history retains its original base without a rewrite.
This clone had no previous local implementation or
unpublished commits; no personal branch names required migration. The existing
TP-1 indicator/trend PRs #2/#3 were merged by their owner during development;
this task did not alter or merge those PRs. No applicable AGENTS.md or
shared schema module was found. The 25 existing strategy/risk/runtime tests pass.

## Provisional boundary

`models.py` consumes the actual `wallex.risk.RiskDecision`. Its `allowed` flag
only represents the existing quantity/notional check and is never full execution
authorization. No finalized shared TradeIntent, ExecutionReceipt, Fill,
AccountSnapshot or AuditEvent schema exists in the fetched histories. These
small module-local consumers require coordination with Setayesh and Roham.

`ExecutionRequest` requires a stable producer-owned intent/client ID, exact
account/instrument/assets, side, explicit LIMIT price/quantity and deadline,
mode, run/trace IDs, decision/version/account references and a supplied
reservation/capacity limit. No IDs, volume, markets, risk profile, fee setting,
tick/step, minimum size or settlement deadline is inferred. Reservation is
validated, never resized: buys reserve quote units covering the explicit gross
order; sells reserve base units. Fee sufficiency and full risk remain upstream
responsibilities. Capacity is an explicit fixture ledger limit, not proof of a
reconciled account balance.

Money and quantity reject floats, integers, numeric strings and booleans at
typed boundaries; callers provide Decimal. Storage serialization uses canonical
decimal strings, aware timestamps normalized to UTC, schema version and
correlation references. A 200-digit/absolute decimal-order bound is a storage
representability guard, not an exchange quantity/price/risk limit. Exact
arithmetic does not depend on Decimal's default 28-digit context.

Unknown fees, source times and Agent/account evidence retain null plus a reason.
`AccountSnapshot` is separate from order receipts and currently represents only
unavailable account balances. It does not infer that an order or account is flat.
Fill records preserve source identity, price, base quantity, fee asset/amount and
timestamps. No real balance collector, exchange adapter or settlement is present.

Modes use `replay`, `shadow`, `live`. There is no earlier `paper` implementation
to map. Synthetic fixtures call the actual risk function with explicitly labeled
fixture settings; they are not signals produced by Strategy, which still returns
NO_TRADE. No TP-1 calculations, market/backtest/shadow runner or AI tools are added.

Use the existing standard-library environment from the repository root:

```powershell
python -B -m unittest discover -s wallex -p 'test_*.py' -v
```

The durable ledger is documented in [../audit/README.md](../audit/README.md).
The injected replay gateway is documented in [HANDOFF.md](HANDOFF.md), including
branch ancestry, consumer contracts, transitions, actual verification and the
remaining producer decisions. Live remains unavailable; no REST/MCP order
submission, credentials or funded account is used.

Run the temporary synthetic UNKNOWN -> restart -> stable-ID reconciliation example:

```powershell
python -B -m wallex.execution.example
```

It submits once, loses the synthetic acceptance response, blocks a new entry
after restart and finds the original order using the same client ID. The output
keeps unknown quantities null, reservations held and sellable balance unknown.
The temporary database is removed when the example finishes. The example never
opens an existing account database or contacts an exchange.

## Scoped HTTPS gateway (disabled by default)

`WallexExchange` in `wallex_http.py` supplies real fixed-origin HTTPS operations:
POST orders (LIMIT BUY/SELL), GET order by persisted client ID, DELETE orders
by `/v1/account/orders/{client_id}`, and GET openOrders/trades/balances-detail. No redirects,
ambient proxies, retry, credential files, main-account fallback or automatic calls.
Official contracts: https://api-docs.wallex.ir/ and
https://developers.wallex.ir/docs/spot-intro and
https://developers.wallex.ir/docs/spot-cancel-order . The current path cancellation
contract is used, without a fallback write to the older query contract.

`WallexGateway` reuses ReplayGateway's durable state machine. Construct a separate
`Ledger(path, mode=Mode.LIVE)`; existing replay databases are refused, never migrated.
Default ReplayGateway still rejects LIVE. Before a new write, authorization must
bind the exact request, current strict Risk pipeline result, approved stop/cost
policy, explicit fee currency, instrument limits, fresh balances/quote and reviewed
prerequisite evidence hashes. An exact order-fingerprint-bound explicit owner confirmation is mandatory
(`AUTHORIZE WALLEX SUBMIT <request fingerprint>`; cancellation uses `CANCEL`). There is no --live flag, environment activation or background trader.
The proof hashes are trusted-backend evidence references, not self-verifying claims;
the existing read-only preflight cannot manufacture missing permission/fee/coverage
or risk authorization. No production evidence producer for those missing facts is
claimed. Fresh authenticated sub-account scope and balance reads are rechecked.
Actual HTTPS POST/DELETE also require a short-lived account/client-bound permit.

`authorize_entry -> submit -> reconcile/recover_account -> cancel (if partial) ->
authorize_exit -> submit -> reconcile -> settle` uses the same SQLite intent/fill
tables. Local fill IDs hash each per-order economic row plus identical-row occurrence;
they are NOT venue fill IDs. Full snapshot quantity must match executedQty. Unassigned
account trades never supply guessed fills. Rejection, malformed/missing fills, fees,
404, timeout or conflicting observations fail closed; uncertain POST is never retried.
Protective SELL is bound to confirmed acquired base (minus fees), persisted stop,
explicit price floor and current exposure. Exact terminal base/quote balance deltas
including fees are required before releasing reservations. TMN is not converted to IRR;
any dust, external movement or frozen funds blocks settlement. Mock status is MOCK_SETTLED.

Focused network-mock verification:
`python -B -m unittest wallex.execution.test_wallex_live -q`.
No real order has been sent. `risk/live_admission.py` provides a separate explicit
execution-admission check reusing the unchanged diagnostic pipeline and Risk Gate.
Real transport requires current collected venue evidence, reviewed permission/credit/
fee-asset/equity artifacts, persisted daily-loss/high-water state, source-event-fresh
quotes, and a hash-matched genuine AI REVIEW read back from the existing Audit ledger.
A pass alone never authorizes a POST. Missing evidence and AI veto fail closed.
Sticky loss breaches survive recovery and explicit UTC day changes; wallet balance
is never substituted for equity. The demo does not create these reviewed artifacts.

Read-only readiness (existing root .env; no prompt, order, package activation or fallback):
`python -B -m wallex.execution.preflight --connect`.
Actual GET checks verified maker/taker rates, decimal increments/minimums, and two
matching account captures. `orders/trades` return `has_more_pages=false`; the unusual
empty `openOrders` page (`per_page=0`) alone proves nothing and is cross-checked against
the complete bounded all-orders capture. This is not atomic exchange admission.
Official v1 market docs: https://api-docs.wallex.ir/ . `stepSize`/`tickSize` count decimal
places; `isMarketTypeEnable` is not interpreted as permission for the prop account.

External evidence still required: documented GETs inspected do not expose key trade
ACL, fee debit-asset guarantees, or credit loan/collateral/expiry authorization.
BTCTMN `maxQty` is not returned; public `/v1/depth` contains no market event timestamp.
Wallex must provide account-bound permission/eligibility, missing limit/fee policy
and a timestamped executable quote contract. No permission is inferred from GET 200.
No exact order ticket is issued while these facts or independent equity history are absent.

Reproducible isolated HTTP simulation (never actual credentials or network):
`python -B -m wallex.execution.simulation --simulate`.
It exercises the actual gateway/ledger: timeout -> recovery -> partial/full BUY ->
protective SELL -> fee-aware TMN settlement. The demo additionally shows a partial
SELL, explicit cancellation, residual exit, and settlement across all child fills.
Repeated submits of an identical durable request return its receipt without reposting,
including after restart when the account is no longer flat. Confirmed canceled exit
reservations may release only their residual hold; parent exposure stays held.
Stop inspection pauses entries on gaps, uncertain exits and timeouts. A LIMIT sell
can remain unfilled; cancellation/repricing outside a separately approved exact
procedure is not automatic. `inspect_protection` never submits orders.
