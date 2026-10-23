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

The default nine-route dashboard now connects to the Python public observer
API at `/api/observer`; Vite proxies `/api` to http://127.0.0.1:8765. Start the
API/public worker using [the connected dashboard runbook](../docs/OBSERVER_DASHBOARD.md).
Refresh is automatic every 15 seconds with bounded timeout and explicit loading,
error, stale and unavailable states. No DEMO financial fallback exists.

Open `/?view=demo` explicitly for the separate legacy presentation screens/manual
report importer. Synthetic portfolio, price, credit and decision values are
labelled DEMO. They never appear in the default connected view. Private balances,
orders, P&L and credit fields stay unavailable until a trusted authenticated
account service exists. No browser credentials or Wallex requests are supported.
Theme, RTL and navigation remain shared. Imported legacy reports stay in memory.

Essential browser checks:

```powershell
npx playwright install chromium
npm test
# Or use an existing Edge installation:
$env:PW_BROWSER_CHANNEL = "msedge"
npm test
```

`npm run check` validates strict TypeScript; build also runs this check.
Static-only deployment builds `frontend/dist` but cannot supply the real API.
For connected production, use the root Dockerfile/Python service and persistent
observer volume described in the runbook. No frontend secrets or environment
variables are required; API and UI share one origin.
Hash navigation preserves direct links such as `/#decision-center`.

Pending: trusted read-only runtime backend, verified account/risk contracts and
reviewed real execution prerequisites; there is no live trading path.
Green demo indicators never grant permission to trade.
