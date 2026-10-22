# Runtime foundation

Purpose: a minimal immutable runtime state model. It runs no trading loop,
schedules no work, and creates no threads or processes.

Ownership: Setayesh owns this directory, `wallex/strategy/`, and
`wallex/risk/` only. This package does not implement teammate-owned shared
contracts, market, backtest, shadow, execution, or audit modules.

Provisional interfaces: `RuntimeState` supports `STOPPED`, `STARTING`,
`RUNNING`, `DEGRADED`, and `STOPPING`. `Runtime()` defaults to `STOPPED`.
`transition(target)` returns a new immutable model and rejects invalid
transitions. State labels do not activate services. Direct construction at
another valid state is possible for modeling, with live execution still off.

```text
STOPPED  -> STARTING
STARTING -> RUNNING | DEGRADED | STOPPING
RUNNING  -> DEGRADED | STOPPING
DEGRADED -> RUNNING | STOPPING
STOPPING -> STOPPED
```

`live_trading_enabled` is always false, has no configuration switch, and
cannot be enabled through the interface. `execute_order(payload)` always
raises `LiveTradingDisabledError` in every state, without inspecting or
forwarding the payload. No exchange client or executor is present.

Dependencies awaiting teammates: approved lifecycle and health contracts,
startup/shutdown policy, market readiness and freshness, risk readiness,
execution interfaces, and audit/event schema. This internal model is not
a shared contract or production supervisor.

Safety limitations: no background trading, network calls, environment or
credential loading, real orders, or automatic recovery. No persistence,
synchronization, resource management, or exchange integration is implemented.
A future live mode requires separate explicit team-approved implementation;
moving to `RUNNING` never enables it in this foundation.

Planned integration: map team health/readiness events into lifecycle
transitions and expose state to future monitoring/audit adapters. Wire
strategy and risk only after their approved contracts exist. Teammate-owned
execution remains separate; there is no execution callback or adapter here.

Dependencies: Python 3.11 standard library only; no third-party packages.
From the repository root, test without bytecode files:

```text
python -B -m unittest discover -s wallex -p test_runtime.py -v
```

## Offline Trade Credit evidence

`credit_account.assess_credit_account(CreditEvidenceInputs(...))` consumes only
stable sanitized payloads for account list, active package and detailed balances.
It accepts no key, authentication headers, HTTP client, clock or execution hook.
The bundled JSON fixture is entirely synthetic, including its active-status
label, amounts, identity and timestamps; it is not a captured private response.

Supply an explicit Trade Credit binding, every response's sub-account scope,
completeness and observed UTC Unix seconds, an `as_of`/maximum age, exact asset
units and local field interpretations. Missing evidence, wrong owner/account/
package identity, inactive/suspended state, stale/future observations, expiry,
ambiguous currency/thresholds or inconsistent balance/fee arithmetic reject the
snapshot. Expiry equality blocks; age-limit equality is allowed. No rounding,
float conversion, implicit FX or wall-clock freshness is used. UTC plan dates
must have whole-second precision. No actual provider active-status enum is
invented: the active label is required caller evidence, and known terminal labels
cannot be admitted by that configuration.

The current local interpretation supports threshold claims in account-value
quote units and an explicit `finalPayback`-as-total-due debt claim. These meanings
and fee currencies are NOT approved Wallex semantics. Unsupported/missing meanings
fail closed; `loan` is never silently substituted for outstanding debt. Returned
frozen balances/terms preserve claims; `available_quote` is reported funds only.
`risk_equity` is permanently None. No Risk/Audit/Execution logic or existing
models are changed; Execution's placeholder AccountSnapshot cannot represent
these balances, so no false conversion is provided.

Even a locally valid snapshot has `blocks_new_entries=True`,
`credential_scope_verified=False`, `executable_ready=False` and `NO_TRADE`.
Request-scope labels do not prove the existing API key's account/permissions.
Observations do not establish an atomic portfolio or settled balances. Account
provenance, approved credit-risk policy, fees/thresholds and atomic admission
remain pending; see `docs/WALLEX_CREDIT_API_AUDIT.md`. No key was read or requested.

```text
python -B -m unittest wallex.infra.test_credit_account -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```
