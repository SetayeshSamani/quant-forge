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
| Tests | Full 604 Python tests and 11 browser tests passed; TypeScript/build passed | Python runtime 3.14.7; all 604 passed with zero failures/errors/skips; 9 focused integrated tests also passed on container Python 3.11.17 |
| Docker/Liara | Dockerfile, OCI revision, healthcheck, SIGTERM, /data instructions | Official image pull/build now succeeded; actual public collection/API/UI/refresh on container8080 verified. Liara not deployed |
| Safe stop/persistence | Native Ctrl+C recorded STOPPED and preserved public observations; SIGTERM handler regression | Docker SIGTERM exit0, persisted /data observation and restart verified |
| AI research veto | Actual offline TP-1 -> bounded BoxAPI structured REVIEW/NO_TRADE -> hash/timestamp/model audit -> Decision Center | Mock integration verified; real inference remains UNAVAILABLE until owner-run evidence. REVIEW is never trading approval |

## AI status — evidence required

`wallex.ai_provider.tp1_review` revalidates saved public source hashes and calls
existing TP-1/Shadow diagnostics. Only an actual technical candidate can reach
BoxAPI, at most one unique call per invocation, with exact Decimal strings and
no account data. REVIEW retains a research candidate; NO_TRADE vetoes it. Counts
are separate from deterministic technical eligibility. Risk/credit stay UNVERIFIED;
the original gated `advise()` behavior and all execution controls are unchanged.

Owner opt-in uses a hidden local key prompt. Model, evidence/source hashes,
cutoff, request/completion timestamps, structured reason codes and source commit
are journaled, never keys, headers or raw replies. Timeout, quota/auth failure or
malformed output leaves the candidate unreviewed, never approved. Decision Center
labels synthetic transports separately and displays historical evidence without
claiming current market freshness. No genuine inference is claimed until its
owner-run result exists. Socket timeout is bounded; a hard total network deadline
and managed production credential provisioning remain limitations.

## Live execution assessment ? NO-GO

| Prerequisite | Actual status / blocker |
| --- | --- |
| Dedicated Hackathon account and key | Owner-attested prop key; discovery/scoped read responses 200. Independent response-account echo and key trade ACL not verified by those reads |
| Funds, fees, limits, exposure | Positive quote flag only, never equity. Actual fee funding, min/max notional/quantity, tick/lot rules, credit eligibility and complete/atomic open-order/exposure coverage unresolved |
| Risk admission | Checked synthetic replay reuses sizing/loss/portfolio pipeline, Pe, quote age, explicit scope/ACL/market assertions, fee funding, instrument limits and self-trade checks. All real attestations remain unavailable |
| Durable IDs and intent | Stable replay identities exist; Ledger enforces mode='replay'. No reviewed durable LIVE intent/submission path |
| Fills and crash recovery | Replay integration verifies lost-response recovery, partial fills and no duplicate submission using existing durable IDs. Real venue identities, account reconciliation and settlement remain unresolved |
| Stops/exits and credit | Read-only replay stop monitor uses deduplicated confirmed fills minus base fees and blocks on unresolved entry/exit/settlement. No live SELL adapter or credit-expiry/liquidation lifecycle |
| TMN settlement | No IRR/TMN conversion is inferred; actual fee/settlement currency, debit timing and authoritative settled funds require verification |
| Local activation | NOT APPROVED. No speculative order, cancellation, transfer or account write is sent |

Read-only success, a technical candidate or positive simulated P&L cannot open
these gates. Live activation requires every prerequisite verified plus separate
explicit owner authorization. Ship tonight without claiming the unimplemented
150-point trading-performance component. Continuing actual trading through
October 26 is subject to official competition rules and these gates, never an
automatic background activation.

## متن دموی پنج‌دقیقه‌ای

| زمان | متن و نمایش |
| --- | --- |
| ۰:۰۰–۰:۳۵ | «QuantForge مسیر دادهٔ واقعی تا ارزیابی قابل حسابرسی TP-1 را نشان می‌دهد. معاملهٔ واقعی فعال نیست و سود یا امتیاز ساختگی گزارش نمی‌کنیم.» معماری را نشان دهید. |
| ۰:۳۵–۱:۲۵ | داشبورد پیش‌فرض، health و SHA نسخه، تعداد واقعی H1/M15، bid/ask و cutoff را نمایش دهید. «refresh هر ۱۵ ثانیه است؛ موجودی و سود خصوصی ناموجودند.» |
| ۱:۲۵–۲:۱۵ | مرکز تصمیم و علت هر رد را نشان دهید. «روند H1، کراس M15 و فاصلهٔ ATR باید هم‌زمان معتبر باشند. کاندیدای فنی مجوز معامله نیست؛ Pe از close حدس زده نمی‌شود.» |
| ۲:۱۵–۳:۰۰ | manifest، hash منبع و ژورنال Shadow/Backtest را نشان دهید. «کارمزد و slippage فرض شبیه‌سازی‌اند؛ صفر fill اثبات سودآوری نیست.» مثال دو fill فقط SYNTHETIC را جدا معرفی کنید. |
| ۳:۰۰–۳:۴۰ | پنل AI را باز کنید. فقط در صورت وجود inference واقعی، مدل/hash/زمان و REVIEW یا وتو را نشان دهید؛ در غیر این صورت صریحاً «AI ناموجود است؛ تست mock اثبات inference واقعی نیست.» |
| ۳:۴۰–۴:۲۰ | «خواندن حساب ۲۰۰ بوده، اما مجوز معامله، fee، پوشش سفارش‌ها و تسویه تأیید نیست. پذیرش replay، partial fill و crash recovery تست شده؛ خروج واقعی و فعال‌سازی ممنوع باقی مانده‌اند.» |
| ۴:۲۰–۵:۰۰ | تست‌ها، پایداری /data، shutdown و تطابق SHA منبع با health/image را نشان دهید. وضعیت واقعی Docker/Liara را بگویید؛ فقط URL واقعاً بررسی‌شده را نمایش دهید. پایان: محدودیت‌ها و دستورات بازتولید. |

## Final submission actions (owner/reviewer)

1. Review release-preparation PR, merge normally into dev/quantforge; preserve
   commits/branches. Then review the draft dev/quantforge -> main milestone PR.
2. Merge that stable PR only after approval; no squash/rebase/force push required.
   Select and check out the exact stable commit on the build machine. Re-run the
   commands in RELEASE_REPRODUCIBILITY.md and stamp artifacts/image with that SHA.
3. Deploy the reviewed exact-SHA Docker image to Liara: port8080, one instance,
   persistent /data and explicit public-fetch command. Local container startup,
   collection, refresh and stop/restart passed; repeat health/source checks on the
   actual deployed URL. No credentials or private APIs belong in this public app.
4. Record approximately five minutes; verify source SHA equals served health/image
   revision and evidence manifest. Upload source and video before **2026-10-23
   23:50 Asia/Tehran**. Public URL, portal upload and rule compliance are manual;
   no deployment or submission has been performed by this task.
