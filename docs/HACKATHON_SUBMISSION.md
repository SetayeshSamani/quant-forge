# QuantForge ? Wallex hackathon submission

## Submission identity and scope

Submit the reviewed final commit, source archive, deployed URL (when verified),
public evidence manifest and five-minute recording together. The exact commit is
recorded by the evidence CLI in `manifest.json`, by the Docker OCI revision label
and by `GET /api/health.source_commit`; these must agree. No public deployment is
claimed yet. Current release preparation starts from owner-merged PR #28,
use the full actual `git rev-parse HEAD` for the release ID, not a guessed or abbreviated hash.

The owner-provided rubric totals 300 points: trading performance 150 and technical
quality 150. No score is self-awarded. Live trading/performance is **NOT
DEMONSTRATED**; this submission demonstrates a real-public-market observer,
deterministic TP-1 diagnostics, fee-aware simulation infrastructure, auditability,
and a connected Persian RTL dashboard. Every executable action is NO_TRADE.

## What actually works

Wallex public M1/native H1/depth -> strict validation and independent gap-safe M15
segments -> causal TP-1/Shadow/Risk diagnostics -> durable SQLite public audit ->
GET-only sanitized HTTP API -> automatically refreshing React dashboard.

All nine routes render using public evidence or explicit unavailable states.
Synthetic financial presentation is isolated at the explicit `?view=demo` URL,
never a fallback. Private account data, keys and IDs are not exposed by the API.
Owner-run scoped account discovery, balances and open-order GETs returned 200,
but this does not establish trading permission, complete exposure or equity.

## Actual TP-1 logic (long spot candidates only)

- Inputs: completed, consecutive UTC candles; at least 250 H1 and 21 M15 bars.
  Gaps are rejected/segmented, never joined, filled or treated as future history.
- EMA uses an SMA seed and alpha 2/(N+1). ATR14 is Wilder-smoothed True Range.
  Price arithmetic uses Decimal with fixed contexts.
- H1 trend: EMA20[h] > EMA50[h] AND EMA20[h] > EMA20[h-3].
- M15 pullback: Close[t-1] <= EMA20[t-1], Close[t] > EMA20[t], and
  0 < Close[t]-EMA20[t] <= ATR14[t]. Both H1 and M15 conditions define only a
  technical candidate, never permission to enter.
- Proposed initial stop: min(lowest Low of latest 3 M15 bars - 0.1*ATR14,
  Pe - 1.5*ATR14). Pe is an explicitly supplied maximum permitted BUY price,
  never automatically the close, ask or eventual fill price.
- Missing quote/depth freshness, Pe, fees, account/credit state, approved risk,
  precision/limits or authorization blocks execution. Position sizing, loss and
  portfolio modules remain diagnostic; no Gate/LIVE path is activated.

## Evidence matrix

| Claim | Reproducible evidence | Limitation |
| --- | --- | --- |
| Real public ingestion | Captured M1/H1 bytes, original SHA256s, UTC cutoffs and API public snapshot | Volume base units are a caller assumption; market event freshness unavailable |
| TP-1 assessment | `offline-tp1.json`, per-segment `shadow-*/evaluations.jsonl` and `events.jsonl` | Technical candidate is not execution approval |
| Fee-aware TP-1 diagnostic backtest | `backtest-*/run.json`, summary, events, equity, fills and Shadow comparison | Actual provider remains NO_TRADE: zero fills/P&L is not strategy performance |
| Nonzero fee/slippage accounting | Existing separately labelled SYNTHETIC two-action harness | Two hypothetical fills, fee 0.2, slippage 0.4, net -0.6 QUOTE; not TP-1 profit or account evidence |
| Timestamped rejection/audit trail | Shadow events carry cutoff/run/event IDs; public observer SQLite records lifecycle/decisions | Local public partition is not an exchange account; no real-order journal claimed |
| Connected frontend/API | Native port 8080 /health, /observer and UI; 15-second refresh tests | Private account endpoints absent; authentication required before adding them |
| Tests | Full 599 Python tests and 11 browser tests passed; TypeScript/build passed | Python runtime 3.14.7; 3.11 syntax checked, container runtime not verified |
| Docker/Liara | Dockerfile, OCI revision, healthcheck, SIGTERM, /data instructions | Docker daemon started; official Python layer pull returned HTTP403, image startup NOT verified |
| Safe stop/persistence | Native Ctrl+C recorded STOPPED and preserved public observations; SIGTERM handler regression | Container stop/restart not verified because image pull failed |
| AI | Existing bounded BoxAPI client and mock tests reviewed | Actual candidate contribution NONE; no live provider call or AI performance claim |

## AI status ? no misleading agent claim

TP-1 candidates are deterministic. The BoxAPI client is isolated and not called
by the observer, candidate evaluator or Shadow provider. Its contract requires
VERIFIED data quality, risk and credit before any request. Real credit/risk and
source-event freshness are unverified here, so attaching the provider by inventing
VERIFIED states would bypass an existing guard. No such integration was made.
Provider quota/availability is also unverified; an owner-reported prior request
limit 403 is not success evidence. Mock tests prove client bounds/fail-closed
handling, not actual model contribution. No API key was requested or used.

## Live execution assessment ? NO-GO

| Prerequisite | Actual status / blocker |
| --- | --- |
| Dedicated Hackathon account and key | Owner-attested prop key; discovery/scoped read responses 200. Independent response-account echo and key trade ACL not verified by those reads |
| Funds, fees, limits, exposure | Positive quote flag only, never equity. Actual fee funding, min/max notional/quantity, tick/lot rules, credit eligibility and complete/atomic open-order/exposure coverage unresolved |
| Risk admission | All current assessments diagnostic; executable_ready=False, no approved current decision/admission token |
| Durable IDs and intent | Stable replay identities exist; Ledger enforces mode='replay'. No reviewed durable LIVE intent/submission path |
| Fills and crash recovery | Venue-stable fill identity, uncertain-submit recovery, deduplication and balances/order/settlement reconciliation unresolved |
| Stops/exits and credit | Proposed stop calculation exists; no real exit monitoring/order lifecycle, expiry/liquidation management or restart recovery |
| TMN settlement | No IRR/TMN conversion is inferred; actual fee/settlement currency, debit timing and authoritative settled funds require verification |
| Local activation | NOT APPROVED. No speculative order, cancellation, transfer or account write is sent |

Read-only success, a technical candidate or positive simulated P&L cannot open
these gates. Live activation requires every prerequisite verified plus separate
explicit owner authorization. Ship tonight without claiming the unimplemented
150-point trading-performance component. Continuing actual trading through
October 26 is subject to official competition rules and these gates, never an
automatic background activation.

## Five-minute demonstration

| Time | Recording |
| --- | --- |
| 0:00?0:35 | Problem, honest scope, architecture. State NO_TRADE and absence of live performance |
| 0:35?1:25 | Default connected dashboard, API health/source revision, real public counts/prices/cutoff and refresh; private fields unavailable |
| 1:25?2:15 | Decision Center: actual H1/M15 reasons and technical candidate distinction. Explain trend, pullback and proposed stop formulas |
| 2:15?3:10 | Evidence manifest, source hashes, real Shadow timestamped rejection journals, matching diagnostic backtest. Show separate SYNTHETIC cost harness with fees/slippage, not profit claims |
| 3:10?3:50 | Risk/Credit and exact live blockers; owner-run read access is not order permission; AI contribution explicitly NONE |
| 3:50?4:30 | Health/lifecycle, safe stop/persistent evidence, tests; state Docker registry403 blocker and deployment status truthfully |
| 4:30?5:00 | Reviewed source SHA, source/evidence archives, reproducibility commands and final limitations. Show deployed URL only after actual verification |

## Final submission actions (owner/reviewer)

1. Review release-preparation PR, merge normally into dev/quantforge; preserve
   commits/branches. Then review the draft dev/quantforge -> main milestone PR.
2. Merge that stable PR only after approval; no squash/rebase/force push required.
   Select and check out the exact stable commit on the build machine. Re-run the
   commands in RELEASE_REPRODUCIBILITY.md and stamp artifacts/image with that SHA.
3. Resolve Docker Hub layer HTTP403 on the build host using authorized registry
   access or an owner-approved source, then verify image startup, /data persistence,
   API/frontend refresh and stop on Liara. No TLS/authentication workaround is used.
4. Record approximately five minutes; verify source SHA equals served health/image
   revision and evidence manifest. Upload source and video before **2026-10-23
   23:50 Asia/Tehran**. Public URL, portal upload and rule compliance are manual;
   no deployment or submission has been performed by this task.
