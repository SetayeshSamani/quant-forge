# QuantForge — Wallex Agentic AI Hackathon

QuantForge demonstrates a cost-aware TP-1 Trend Pullback workflow with explicit
market-data uncertainty and fail-closed risk/credit diagnostics. This submission
is an **offline research and read-only UI demo**, not a live trading system.
Every executable action remains **NO_TRADE**, `executable_ready=false`.

## Architecture and features

- `frontend/`: React + TypeScript + Vite, Persian RTL, reusable components,
  bundled Vazirmatn, persistent dark/light themes and responsive navigation.
  Screens: Overview, Agent Decision Center, Credit & Risk.
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
The static frontend displays labelled presentation data and a separate reported
historical summary; it does not call Python, Wallex or an AI provider.

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

Open the printed local URL. No API key, environment file, backend or database is
needed. Build runs strict TypeScript checking. Optional existing browser smoke
checks: `npm test` with Playwright Chromium installed, or set
`$env:PW_BROWSER_CHANNEL = "msedge"` to use installed Edge.

## Supervised public-market worker

A real-public-market worker now reuses the existing TP-1/Shadow diagnostics with
persistent Audit events, run-once/continuous modes and controlled shutdown.
It remains NO_TRADE; authenticated preflight is a separate owner-run hidden prompt.
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
All other UI portfolio, price, credit and decision fixtures remain **DEMO**.

## Deploy the static submission

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
5. Optionally show the existing Python offline report or labelled synthetic CLI
   walkthrough. End with NO_TRADE and the deployment URL, once available.

## Limitations

No live orders, account writes or automatic execution. The UI/public worker uses
no credentials; a separate explicit interactive GET-only preflight is available.
Credit-plan authentication (prop HTTP 401) remains unresolved. Account balances,
credit evidence, fees, quote freshness and execution permissions are unverified.
Public depth receipt time is not provider event time or verified freshness.
MarketFrame schemas remain provisional; technical eligibility never authorizes
trading. No backtested profitability, completed trades or verified capacity is
claimed. The demo is ready for presentation, not production trading.
