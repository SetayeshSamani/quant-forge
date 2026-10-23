# Supervised Wallex public-market runtime

## Delivered scope and exact live blockers

This is a functioning **real-public-market observation worker**, not an enabled
trading agent. It reuses history/depth transports, gap-aware offline TP-1/Shadow/
Risk/Credit diagnostics, Runtime states and the existing durable Audit Ledger.
It makes no private request during observation, never creates an ExecutionRequest
or intent, and never calls submit/cancel. All results remain NO_TRADE and
executable_ready=false. Optional UI work was deferred to keep P0 focused.

Live admission cannot safely be implemented by wiring an HTTP order endpoint:
- `wallex/risk/pipeline.py`: permanently diagnostic; no approved risk/admission token.
- `wallex/execution/gateway.py`: ReplayGateway rejects LIVE and real exits; its
  transport requires synthetic replay evidence.
- `wallex/audit/ledger.py`: persisted intents enforce `CHECK(mode='replay')`.
- Real exit policy, venue-stable fill identity/deduplication, uncertain-submit
  recovery, account/fill/balance reconciliation and durable loss state are unresolved.
- Account scope, available funds, existing exposure/open orders, actual fees,
  instrument limits, eligible credit markets and owner activation remain unverified.
- The public depth contract supplies no reliable source event time; quote freshness
  and independent market identity cannot be upgraded into COMPLETE/approved evidence.

No execution guard, TP-1 rule, risk calculation, replay system or MarketFrame
contract was changed. No nonfunctional write-adapter skeleton was added.

## Account preflight (OWNER runs interactively)

Run from the repository root with Python 3.11+ (validated runtime: 3.14.7).
Supply your already verified identifiers locally; placeholders below are NOT IDs.
Never send the key in chat or pass it as an argument/environment/frontend variable.

```powershell
$state = "$env:LOCALAPPDATA\QuantForge\agent-observer"
python -B -m wallex.infra.agent_runtime preflight --state-dir "$state" --symbol BTCTMN --base BTC --quote TMN -- --connect --sub-account-client-id YOUR_CLIENT_ID --owner-user-id YOUR_USER_ID --package-id YOUR_PACKAGE_ID --user-package-id YOUR_USER_PACKAGE_ID --currency TMN --plan-type YOUR_PLAN_TYPE --expected-plan-status YOUR_STATUS
```

The existing hidden getpass prompt and strict account/owner/package checks are
reused. Only the existing three GET endpoints can run. Failure, including prop
401, is preserved; partial checks never become approval. Add `--diagnose-prop` at
the end to request discovery + active plan only. No secret, account identifier or
raw account response is written to the observer journal. Currency must match the
observer's quote. A process already holding this state directory's observer lock
must be stopped before preflight.

A successful read does not establish Hackathon mode or trade permission. The owner
must verify key issuance while the intended Hackathon/prop account was selected,
expiry and read/trade permissions in Wallex; withdrawal must remain disabled.
Official account separation requires the dedicated credit key AND sub-account
header: https://developers.wallex.ir/docs/spot-intro . The order `client_id` is not
that sub-account ID. Key permissions: https://developers.wallex.ir/docs/create-api-key .

The prop 401 is endpoint-specific, not proof that spot trading is impossible.
Ask Wallex to confirm that key's active-package read ACL and account/package
association. Never substitute main/margin credentials or use cookies/session tokens.
Until the complete account/fee/market/exposure/recovery contracts are verified and
live integration separately reviewed and approved, real-money mode is unavailable.

## Run once, continuously, inspect and stop

```powershell
# Public-only; NO credentials. The base-volume flag is an explicit UNVERIFIED
# caller assumption for existing price diagnostics, not independent unit verification.
python -B -m wallex.infra.agent_runtime observe --fetch-public --once --state-dir "$state" --symbol BTCTMN --base BTC --quote TMN --volume-unit base
# Continuous: remove --once. Optional --max-cycles N bounds scheduled iterations.
python -B -m wallex.infra.agent_runtime observe --fetch-public --state-dir "$state" --symbol BTCTMN --base BTC --quote TMN --volume-unit base
python -B -m wallex.infra.agent_runtime status --state-dir "$state"
```

Omit `--volume-unit base` to leave units unknown and block technical evaluation.
`--interval-seconds` is bounded to 60-900 seconds (default 60). Each new completed
M15 cutoff fetches at most three public GETs: six hours M1, 264 native H1 bars,
and depth. Each transport has a five-second socket timeout and existing byte/level
bounds, no credentials/redirects/retries. Failed observations may be polled again
on the next scheduled iteration; uncertain order submissions are never possible.

Closed-candle cutoff and actual receipt times are recorded separately. Only
consecutive complete M15 segments and causal H1 prefixes reach the unchanged
assessments. Historical step recomputation is labelled retrospective diagnostics,
not live executable evidence. Pe is never taken from candle close or ask. Private
financial/risk inputs are not supplied or inferred. Incomplete data, collection
crossing a cutoff, stale prefixes and network failures remain visibly degraded.

Ctrl+C/SIGTERM requests controlled shutdown after current bounded work; the
worker records STOPPED. The OS writer lock prevents two workers sharing one
state. Restart uses the same directory, preserves all evidence and skips completed
observations at the same cutoff/assumption. Interrupted public reads may repeat;
this is not order recovery. Clock rollback blocks. Audit failures stop the worker.
Status includes persisted lifecycle, heartbeat age, last observation/failure and
sanitized preflight; a stored heartbeat is not proof a process is alive or a market
quote is fresh. RUNNING means the observer loop, never trading readiness.

State must be a dedicated directory outside the repository. SQLite events reuse
Ledger.record_event; its mandatory account_id column uses an explicitly labelled
PUBLIC_OBSERVER_NOT_AN_EXCHANGE_ACCOUNT partition, never an exchange identity or
order reservation. Public raw JSON and source hashes are retained under
observations/, alongside each machine-readable report. No data is committed to Git.

## Genuine observed status and deployment through Monday

One real public-only cycle on **2026-10-23 at 15:34:27 Iran time** returned HTTP 200
for M1 history, native H1 history and depth. Result: 264 H1, 24 M15, four evaluated
steps, zero eligible, zero replay errors, NO_TRADE. This is a captured observation,
not current freshness or account/trade approval. No real authenticated preflight,
key permissions, balance, open orders, fees or execution were tested by Codex.

Deploy the reviewed commit to a trusted persistent Python process/VM/container:
- Python 3.11+, system clock synchronization, verified TLS and public HTTPS egress
  to api.wallex.ir. No Python dependencies or keys are needed by the public worker.
- A local persistent writable volume for the dedicated state directory; preserve
  both SQLite and raw observations across restarts. One worker per state directory;
  network/shared filesystems and distributed admission are unsupported.
- A supervisor running the continuous command above, working directory the repo,
  restart-on-failure with at least 60 seconds backoff, SIGTERM and >=60 seconds
  shutdown grace, process/heartbeat/error/disk monitoring and rotated stdout logs.
- Keep host, volume and process online through **Monday, October 26, 2026** and stop
  explicitly when desired. There is no automatic expiry or authorization to trade.

Vercel serves only frontend/dist; it cannot run this durable Python worker.
Private preflight is manual, interactive and separate. A public observer restart
requires no credential; this deployment does not provide autonomous account/order
management. Do not deploy real-money execution until all blockers above are resolved.
