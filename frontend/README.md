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
Theme persists locally when storage is available. Unbuilt sections are disabled.
Overview, Credit & Risk, and the six-stage decision scenario use synthetic DEMO
presentation fixtures, not account evidence or orders. Decision Center also shows
a separate HISTORICAL / OFFLINE BTCTMN summary reported by the project owner:
264 H1, 24 M15, 4 evaluated steps, 0 eligible, 0 errors, NO_TRADE. The original
artifact, hashes, range and cutoff are not bundled; no live readiness is claimed.
No API client, credentials, trading authorization or executable action exists.
Python modules are unchanged; no backend endpoint is assumed.

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

Future: other approved screens and an agreed read-only backend contract.
Green demo indicators never grant permission to trade.
