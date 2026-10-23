# QuantForge frontend

React + TypeScript + Vite, Persian RTL. Overview follows the supplied approved
`dark/01-overview-dark.png` and `light/overview-light.png` references. The Q mark
is a local vector rendering of the reference; Vazirmatn is bundled locally.
The archive supplied images only; original font/logo source files remain pending.

```powershell
cd C:\git\shared\quant-forge\frontend
npm ci
npm run dev
npm run build
```

Open the URL printed by Vite (default http://127.0.0.1:5173).
Theme persists locally when storage is available. All nine sidebar routes render.
Overview, Credit & Risk, and the six-stage decision scenario use synthetic DEMO
presentation fixtures, not account evidence or orders. Decision Center also shows
a separate HISTORICAL / OFFLINE BTCTMN summary reported by the project owner:
264 H1, 24 M15, 4 evaluated steps, 0 eligible, 0 errors, NO_TRADE. The original
artifact, hashes, range and cutoff are not bundled; no live readiness is claimed.
No API client, credentials, trading authorization or executable action exists.
No backend endpoint is assumed. The six operational screens show explicit
unavailable states or captured reports imported in Connections & Settings:
Strategy & Market, Positions & Orders, Backtest & Analytics, Robot Control,
Alerts & Events, Connections & Settings. Supported files: public worker
`report.json`, public `status` JSON, sanitized `spot_preflight` JSON. Raw/private
account responses and secret/header fields are rejected. Only allowlisted
projections live in memory; reload clears them, with no upload or persistence.
Counts, bid/ask and technical reasons come from the selected report; unknown
balances, P&L, fees and current state are not invented. Imported files remain
CAPTURED / UNVERIFIED. Start/Stop/live Refresh are disabled because no trusted
management API is connected. Use the Python worker on its host; never browser keys.
See [runtime and independent account read preflight](../docs/AGENT_RUNTIME.md).

Essential browser checks:

```powershell
npx playwright install chromium
npm test
# Or use an existing Edge installation:
$env:PW_BROWSER_CHANNEL = "msedge"
npm test
```

`npm run check` validates strict TypeScript; build also runs this check.
Static deployment: Node >=22.12, root `frontend`, install `npm ci`, build
`npm run build`, publish `dist`. No server or environment variables are required.
Hash navigation preserves direct links such as `/#decision-center`.

Pending: trusted read-only runtime backend, verified account/risk contracts and
reviewed real execution prerequisites; there is no live trading path.
Green demo indicators never grant permission to trade.
