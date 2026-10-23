# QuantForge — Wallex Agentic AI Hackathon

QuantForge demonstrates a cost-aware TP-1 Trend Pullback workflow with explicit
market-data uncertainty and fail-closed risk/credit diagnostics. This submission
includes **offline research, a real-public-market observer and read-only UI**,
with real execution disabled.
Every executable action remains **NO_TRADE**, `executable_ready=false`.

## Architecture and features

- `frontend/`: React + TypeScript + Vite, Persian RTL, reusable components,
  bundled Vazirmatn, persistent dark/light themes and responsive navigation.
  Nine routes: Overview, Decision Center, Credit & Risk, Strategy & Market,
  Positions & Orders, Backtest & Analytics, Robot Control, Alerts & Events, Settings.
- `wallex/market/`: strict OHLCV validation, native H1 ingestion, gap-aware M15
  segmentation and research-only public order-book/spread diagnostics.
- `wallex/strategy/`: deterministic Decimal EMA/ATR, H1 trend, M15 pullback,
  initial stops and provisional frame diagnostics.
- `wallex/risk/` and `wallex/infra/`: diagnostic sizing/loss/portfolio checks
  and read-only credit-evidence foundations; missing evidence blocks entry.
- `wallex/shadow/`, `wallex/backtest/`, `wallex/audit/`, `wallex/execution/`:
  offline replay, journals and execution foundations. No live path is enabled.

Saved public data → validation/independent consecutive segments → TP-1 technical
assessment → risk/credit diagnostics → Shadow journal → **NO_TRADE**.
The default frontend polls the real read-only Python observer API every 15
seconds. It displays actual public-market/TP-1 reports, health and blockers.
Private balances, credit, orders and profit stay unavailable. The legacy synthetic
presentation/manual importer exists only at the explicit `?view=demo` URL.
No credentials or private Wallex requests run in the browser. See the
[connected local/API/Liara runbook](docs/OBSERVER_DASHBOARD.md).

## Run the UI

Prerequisite: Node.js >=22.12 (validated with Node 24.21.0).

```powershell
cd frontend
npm ci
npm run dev
# Production build and local production preview:
npm run build
npm run preview
```

Open the printed local URL. Start the public Python API as described in the
runbook for real data; without it the connected view shows an error, not DEMO.
No API key or frontend environment file is needed. Build runs strict TypeScript checking. Optional existing browser smoke
checks: `npm test` with Playwright Chromium installed, or set
`$env:PW_BROWSER_CHANNEL = "msedge"` to use installed Edge.

## Supervised public-market worker

A real-public-market worker now reuses the existing TP-1/Shadow diagnostics with
persistent Audit events, run-once/continuous modes and controlled shutdown.
It remains NO_TRADE; independent authenticated account READ preflight is a
separate owner-run hidden prompt, without package/user IDs or the active-plan route:

```powershell
python -B -m wallex.infra.spot_preflight --connect --symbol BTCTMN --base BTC --quote TMN
```

Select the intended discovered prop account with `HACKATHON N`; verify the masked
identity locally. Both dedicated credit key and account header are used for scoped
balance/order reads. No main-account fallback, write operation or order test exists.
Read success never grants trade permission or risk approval.
See [startup, preflight, status and durable-worker requirements](docs/AGENT_RUNTIME.md).
Live trading is blocked by unapproved risk/admission, replay-only storage/execution,
missing real exit/reconciliation contracts and unverified account/fee/market evidence.

## Offline Python evidence

Use Python 3.11+ and the standard library, from the repository root:

```powershell
# Clearly synthetic, deterministic walkthrough; no network/account calls:
python -B -m wallex.shadow.tp1_demo
# Existing real-data workflow and all required local-file/cutoff arguments:
python -B -m wallex.shadow.offline_tp1 --help
```

See [the offline integration command](docs/OFFLINE_TP1.md) for saved raw M1/native
H1 files. Never join M15 segments across gaps: each decision needs at least
21 consecutive M15 and 250 completed consecutive H1 candles.

The owner-confirmed **BTCTMN HISTORICAL / OFFLINE** result is 264 H1, 24 M15,
4 evaluated steps, 0 eligible steps, 0 errors, **NO_TRADE**. Decision Center shows
only this supplied summary. Original response files, hashes, UTC range/cutoff and
per-step rejection reasons are not bundled; no independent rerun is claimed here.
The original three screens retain **DEMO** fixtures. The six operational pages
use explicitly selected captured reports or unavailable states, separately from DEMO.

## Deployment modes

For the functional connected dashboard, use the root Dockerfile and the
[Python API/persistent-volume runbook](docs/OBSERVER_DASHBOARD.md); Liara preparation
is included, deployment has not been performed. The static-only option below
cannot run the observer/API and needs a separate same-origin reverse proxy.

### Static-only presentation

Use any static Vite-compatible host and select the reviewed release commit/branch:

| Setting | Value |
| --- | --- |
| Repository | `SetayeshSamani/quant-forge` |
| Root / working directory | `frontend` |
| Node | 22.12+ (24 supported) |
| Install | `npm ci` |
| Build | `npm run build` |
| Publish / output directory | `dist` relative to `frontend` |
| Environment variables | None |

Serve at the host's site root over HTTPS. Navigation uses URL hashes, so no API
proxy or server-side route rewrites are needed. Do not deploy the Python tree as
a server and never put credentials in frontend variables. Hosting/project setup,
public URL and upload/submission are manual release steps; no deployment is
performed by this repository change.

## Video walkthrough (about 3 minutes)

1. **Overview:** identify the DEMO banner and disabled trading; show Persian RTL,
   navigation and dark/light mode.
2. **Decision Center:** show the separate HISTORICAL / OFFLINE BTCTMN panel and
   its 264 / 24 / 4 / 0 / 0 summary. Explain that no eligible entry is a valid
   outcome, not a profit claim. Expand the read-only summary.
3. **Decision flow:** explicitly switch attention to the synthetic DEMO scenario;
   inspect Market → Strategy → Risk → Credit → Audit → Execution and its blockers.
4. **Credit & Risk:** distinguish sample values from unverified account evidence;
   explain that wallet balance is not inferred risk equity.
5. In **Connections & Settings**, load the public worker's saved `report.json`.
   Show its actual technical reasons/counts in Strategy & Market and Analytics;
   explain captured timestamps, missing account inputs and disabled host controls.
6. Optionally load sanitized owner-run spot preflight JSON. Positions & Orders
   shows only redacted evidence flags/counts; zero observed orders is not atomic
   flatness or admission. End with NO_TRADE and the deployment URL.

## Limitations

No live orders, account writes or automatic execution. The UI/public worker uses
no credentials; a separate explicit interactive GET-only preflight is available.
Credit-plan authentication (prop HTTP 401) remains unresolved, separately from
the independent scoped read preflight. Its actual result is owner-run; missing
credit evidence, fees, quote freshness and execution permissions remain unverified.
Public depth receipt time is not provider event time or verified freshness.
MarketFrame schemas remain provisional; technical eligibility never authorizes
trading. No backtested profitability, completed trades or verified capacity is
claimed. The demo is ready for presentation, not production trading.

## Final hackathon release evidence

See [competition submission, evidence matrix and five-minute outline](docs/HACKATHON_SUBMISSION.md)
and [exact reproduction/source/Docker commands](docs/RELEASE_REPRODUCIBILITY.md).
The existing fee-aware engine and TP-1 Shadow are composed by
`python -B -m wallex.shadow.release_evidence --help`; real market files stay
outside Git and retain hashes/cutoffs. Simulation costs/capital are explicit
assumptions. TP-1 remains NO_TRADE; no live performance or actual AI contribution
is claimed. Docker Hub layer HTTP403 currently blocks local image verification;
native port8080 UI/API/collection and controlled stop/persistence were checked.
