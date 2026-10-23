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
with `clientOrderId` query, and GET openOrders/trades/balances-detail. No redirects,
ambient proxies, retry, credential files, main-account fallback or automatic calls.
Official contracts: https://api-docs.wallex.ir/ and
https://developers.wallex.ir/docs/spot-intro . The newer cancellation documentation
also offers a path variant; this adapter uses the query variant above, without fallback.

`WallexGateway` reuses ReplayGateway's durable state machine. Construct a separate
`Ledger(path, mode=Mode.LIVE)`; existing replay databases are refused, never migrated.
Default ReplayGateway still rejects LIVE. Before a new write, authorization must
bind the exact request, current strict Risk pipeline result, approved stop/cost
policy, explicit fee currency, instrument limits, fresh balances/quote and reviewed
prerequisite evidence hashes. An account-bound explicit local owner confirmation
is mandatory. There is no --live flag, environment activation or background trader.
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
No real order has been sent. Actual HTTPS admission also requires executable Risk authority; the existing
diagnostic pipeline explicitly returns executable_ready=False, so manually
supplied proof hashes and owner confirmation cannot promote it to LIVE. The mock
lifecycle is labelled HTTP_MOCK and does not establish real risk authorization.
FIRST LIVE entry remains NO-GO pending reviewed executable Risk authority, verified trade
ACL, actual fees and credit market/size rules, complete account/exposure coverage,
current approved Risk/Pe and exit/recovery/settlement evidence, plus separate owner
activation. A LIMIT protective exit can remain unfilled during a gap; automatic
monitoring/repricing and a reviewed fail-safe operator policy are not claimed.
Read-only owner verification reuses `python -B -m wallex.infra.spot_preflight --connect
--symbol BTCTMN --base BTC --quote TMN`, with the existing key in hidden prompt only.
