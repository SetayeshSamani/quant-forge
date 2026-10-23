# Supervised Wallex public-market runtime

## Delivered scope and exact live blockers

This is a functioning **real-public-market observation worker**, not an enabled
trading agent. It reuses history/depth transports, gap-aware offline TP-1/Shadow/
Risk/Credit diagnostics, Runtime states and the existing durable Audit Ledger.
It makes no private request during observation, never creates an ExecutionRequest
or intent, and never calls submit/cancel. All results remain NO_TRADE and
executable_ready=false. The UI can import sanitized captured reports, without
a live management API or browser credentials.

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

## Independent scoped account READ preflight (OWNER runs interactively)

From the repository root, Python 3.11+ (validated runtime: 3.14.7):

```powershell
python -B -m wallex.infra.spot_preflight --connect --symbol BTCTMN --base BTC --quote TMN
```

Enter the dedicated Hackathon/prop account key ONLY at the hidden getpass prompt.
The CLI discovers identities using GET `/sub-accounts`, displays active,
unsuspended prop choices with masked client IDs, and requires `HACKATHON N` to
confirm the intended account and its dedicated key. Cancel if the masked choice
cannot be identified. No manually entered user ID or package ID is needed.
Discovery is refreshed after confirmation; duplicate, changed, incomplete,
unknown-type or inactive identities fail closed. There is no main-account fallback.

The first discovery GET sends only `x-api-key`. Once explicitly selected, both
`x-api-key` and `sub-account-client-id` are required for refreshed discovery and
GET `/v1/account/balances-detail`, GET `/v1/account/openOrders`. Orders are bounded
to three pages / 300 records. Null pagination metadata stays UNVERIFIED, even if
zero orders were observed. The public market GET is `/hector/web/v1/markets`.
The endpoint-specific active-package 401 is not used by this independent path.
The existing credit-plan preflight remains available and strict, separately;
missing plan evidence still blocks credit-risk approval.

Stdout is sanitized JSON: endpoint status codes, identity/status checks, positive
quote-capital/exposure flags, observed order count/coverage, market precision and
explicit blockers. No IDs, exact amounts, order IDs, headers, keys or raw account
responses are returned or saved. Exit 0 means authenticated read collection
succeeded, **not** execution readiness. The key remains ephemeral; no retry,
redirect, proxy, authenticated write or speculative order is performed.

Official scope: https://developers.wallex.ir/docs/spot-intro requires a dedicated
credit key AND the sub-account header. Discovery reference:
https://developers.wallex.ir/docs/trade-credit-active-sub-account-list .
Public official spot specification:
https://developers.wallex.ir/assets/files/spot-b9d06896d1009adc9ebc69b9a5495e7e.json .
Owner/user identity is a discovered consistency check, not an order parameter.
Order `client_id` identifies an ORDER, not the selected account.

Read success cannot prove trade permission, Hackathon mode, independently echoed
response scope, fee funding, complete instrument/credit eligibility or atomic
reconciliation. Verify key issuance in the intended account, expiry, read/trade
permissions and disabled withdrawal with Wallex. If a scoped read returns 401/403,
ask Wallex to verify that key's ACL for the exact failed route and account binding;
never substitute a main/margin key or session token. Retain the exact redacted
failure; no authentication workaround is implemented.

A real-order adapter is **not enabled or implemented** in this change: protected
risk, durable intent and exit/recovery contracts listed above cannot issue valid
live authorization. No synthetic replay decision or wallet balance is promoted
into approval. Local activation and these missing prerequisites remain explicit
blockers irrespective of the old package endpoint's outcome.

## Run once, continuously, inspect and stop

```powershell
# Public-only; NO credentials. The base-volume flag is an explicit UNVERIFIED
# caller assumption for existing price diagnostics, not independent unit verification.
$state = "$env:LOCALAPPDATA\QuantForge\agent-observer"
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

## Frontend evidence and release

All nine sidebar routes render. Overview, Decision Center and Credit & Risk keep
labelled DEMO scenarios; the separate historical summary remains OFFLINE.
Strategy & Market, Positions & Orders, Backtest & Analytics, Robot Control,
Alerts & Events, and Connections & Settings display captured report evidence or
explicit unavailable reasons, never fabricated balances, fills or P&L.

In Connections & Settings choose the worker's `observations/.../report.json`, a
sanitized `status` JSON, or the spot CLI's sanitized JSON. Only allowlisted fields
are projected in memory; nothing is uploaded or persisted in browser storage.
Reload clears evidence. Files with secret/header fields are rejected. Imported
files are unverified snapshots, never live freshness or account authorization.
Start/Stop/live Refresh remain disabled with their actual missing backend reason.

Static deployment remains root `frontend`, Node 22.12+, install `npm ci`, build
`npm run build`, output `dist`, no environment variables or API proxy. A trusted
persistent Python worker is separate; browser-hosted authenticated actions and
real-money execution are unavailable. Public worker operations require no key.
