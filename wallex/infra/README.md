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

## Opt-in read-only credit client

`credit_readonly.CreditReadOnlyClient` requires injected `GetTransport`, logical
credential resolver and UTC Unix-second observation clock. Import/construction
performs no HTTP or key lookup. `ReadOnlyConfig(credential_reference=...)`
defaults to `opt_in=False` and `scope=UNVERIFIED`; it sends nothing and does not
invoke the resolver. There is no environment/.env loader or actual key in config.
For FUTURE local read verification, the owner must explicitly attest the matching
Trade Credit sub-account and read permission, set the corresponding scope and
opt in. These are declarations, not proof of the existing key's actual ownership.
No trading/withdrawal permission is inferred or exposed.

Only these exact paths are available, using GET on `https://api.wallex.ir`:

- `/sub-accounts`
- `/v1/prop/user/packages/active`
- `/v1/account/balances-detail`

Every request includes the explicitly selected sub-account and resolved credential
in headers; there is no main-account/key fallback. The optional future
`UrllibGetTransport` enforces the same route allowlist, normal TLS/hostname
verification, no redirects or ambient proxies, request timeout and bounded body
reads. There are no write methods, retries or background work. Mocks use fake
credentials only; no authenticated Wallex call was performed for this feature.
401/403/404/429/5xx, timeout, malformed/oversized JSON, inconsistent scope and clock
failures return safe codes without body/exception/header details or partial bundles.

Discovery has a **one-page budget** plus an explicit row limit. Audited docs do
not specify discovery request paging parameters; partial/inconsistent pages are
rejected rather than inventing page queries or claiming full coverage. Confirm a
provider pagination contract before adding multi-page discovery. JSON duplicate
keys/nonfinite tokens fail. Only required wire fields are retained; messages,
headers and unknown metadata are omitted. Secret reflections in retained fields
reject the read. Request/reply/bundle reprs omit authentication and body data.

A successful `CreditReadBundle` preserves immutable sanitized JSON and each actual
observation time; it contains no credit-risk interpretation. Explicitly call
`bundle.for_assessment(semantics=..., as_of=..., max_age_seconds=...)` to produce
fresh CREDIT-02 inputs, then use the existing `assess_credit_account` if desired.
Collection is not approval: all client results retain `NO_TRADE`,
`executable_ready=False` and `credential_scope_verified=False`. Existing credit
semantics, account provenance, freshness/admission policy and reconciliation
remain unapproved. No actual API key is requested, stored or logged by the client;
only transient request headers can carry a resolver result after explicit opt-in.

```text
python -B -m unittest wallex.infra.test_credit_readonly -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```

## Manual local credit preflight

Run only yourself in an interactive terminal, from the repository root. Replace
all identity/status placeholders with independently verified dashboard/provider
values; IDs and the active-status label are never discovered by guessing. OWNER_ID,
PACKAGE_ID and USER_PACKAGE_ID must be positive integers. Use the actual quote
currency (TMN or USDT). Do not put the API key in arguments, environment or files.

```powershell
python -B -m wallex.infra.credit_preflight --connect `
  --sub-account-client-id CREDIT_CLIENT_ID --owner-user-id OWNER_ID `
  --package-id PACKAGE_ID --user-package-id USER_PACKAGE_ID `
  --currency TMN --plan-type PLAN_TYPE --expected-plan-status PLAN_STATUS
```

`--connect` explicitly opts in and attests the selected credit account's read
access. The key is obtained only by hidden `getpass`; absent interactive input or
an echo fallback aborts. Existing bounded GET-only transport, TLS and no-redirect
policy are reused. Three scoped responses must match the explicit account/owner,
package IDs, plan type, currency and status label; suspended accounts, mismatches
and observations older than 30 seconds fail. Missing identifiers require manual
confirmation before running. No raw responses, IDs, balances or status strings
are printed, persisted or logged: stdout is a fixed-category JSON summary.
Exit 0 means only successful reads and these local checks, never financial health
or proven credential scope. Balance arithmetic, expiry, fees and credit-risk
semantics remain unassessed here; the existing offline adapter requires separately
agreed evidence/semantics. `NO_TRADE` and `executable_ready=False` are permanent.
This command is not run against Wallex during development; tests use mocks only.


### P0 prop endpoint diagnostic (manual only)

Add `--diagnose-prop` to the existing opt-in preflight command. It is mutually
exclusive with `--diagnose-scope` and requests only discovery and active package,
never balances. The owner runs it locally; no real call is made by tests or Codex.

```powershell
python -B -m wallex.infra.credit_preflight --connect --diagnose-prop `
  --sub-account-client-id CREDIT_CLIENT_ID --owner-user-id OWNER_ID `
  --package-id PACKAGE_ID --user-package-id USER_PACKAGE_ID `
  --currency TMN --plan-type PLAN_TYPE --expected-plan-status PLAN_STATUS
```

Replace identifiers, currency and labels with independently verified local values;
never guess or put a key in arguments/files/environment. Existing hidden `getpass`
and interactive-terminal checks apply. Output contains only categorical identity
checks, endpoint/status, classification and a fixed next-step code. HTTP 200 with
invalid evidence remains a failure. HTTP 401/403 do not imply an absent package;
404 semantics remain unspecified. Diagnostic success means readable, unapproved
evidence only; it never verifies financial status, key scope or execution.
Inactive/suspended discovery stops before package/balance requests. See the
[auth review](../../docs/WALLEX_CREDIT_API_AUDIT.md#prop-preflight-authentication-review--2026-10-23)
for the exact provider clarification needed. No retries, auth bypass or account
fallback are added. Normal three-GET preflight and all risk guards remain enabled.
