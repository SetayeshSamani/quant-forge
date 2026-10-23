# Connected public observer dashboard

The default nine-route UI polls the actual Python API every 15 seconds, with a
five-second request timeout, manual refresh, loading/error/unavailable/stale states.
There is no DEMO fallback. `?view=demo` explicitly opens the separate legacy
presentation/manual-file view; never interpret its financial fixtures as account data.

## Local run (Python 3.11+, Node 22.12+)

From repository root, terminal 1:

```powershell
$state = "$env:LOCALAPPDATA\QuantForge\dashboard-api-check-20261023"
python -B -m wallex.infra.observer_api --state-dir "$state" --frontend-dir frontend/dist --fetch-public --symbol BTCTMN --base BTC --quote TMN --volume-unit base
```

`--fetch-public` explicitly opts into the existing supervised public observer.
No flag means API-only, no outbound request. The base-volume flag remains an
UNVERIFIED caller assumption, not provider unit verification. State must be a
dedicated directory outside the repository. Ctrl+C/SIGTERM stops API and observer.
Do not run a second observer writer on the same directory.

Terminal 2:

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev -- --port 5173 --strictPort
```

- Vite dashboard: http://127.0.0.1:5173 ; `/api` proxies to 127.0.0.1:8765.
- HTTP API: http://127.0.0.1:8765/api/observer ; health: `/api/health`.
- Production UI: build with `npm --prefix frontend run build`, then open
  http://127.0.0.1:8765 using the same server's `--frontend-dir frontend/dist`.

The API reads existing SQLite state in a read-only consistent transaction; it
never initializes Ledger or collects data on a request. Only public observer
partition/kinds are queried. A bounded allowlist projection includes counts,
causal cutoffs, public bid/ask/spread, per-step technical reasons, source hashes,
local timestamps, lifecycle/heartbeat and exact readiness blockers. No raw files,
trace/account IDs, preflight records, private balances or credentials are served.
GET/HEAD only; writes return 405, unknown/private paths return 404, missing/invalid
observer state returns sanitized 503. No permissive CORS or frontend key exists.

RECENT_CAPTURED means local observation age <=1200 seconds; older reports are
STALE. Neither this nor a heartbeat proves source-event freshness or current
process liveness. Stopped lifecycle is shown separately from recent market data.
Historical closed-candle recomputation is not an executable signal. Market
identity/event freshness, volume units, Pe, risk equity, fees, private account/
credit state and permissions remain unverified. All actions stay NO_TRADE.

All nine routes use public backend evidence or explicit unavailable states.
Overview and Strategy/Market show actual counts and depth; Decision Center shows
actual technical reasons and step journal; Analytics shows counts without fake
profit; Robot/Alerts/Settings show reported health, blockers and connection state.
Positions/Orders and Credit/Risk have no authenticated private API and therefore
show no balances, P&L, order-flatness claims or invented capacity. Such an API
requires a separately authenticated trusted server boundary; it is not exposed
by this public service. Start/Stop controls remain disabled; refresh is an actual GET.

## Liara preparation, NOT deployed

A multi-stage root Dockerfile builds Vite with Node 24 and serves the resulting
static UI plus API with Python 3.11 on 0.0.0.0:8080. `.dockerignore` permits only
required frontend/source files; no Git, local state, credentials or downloaded
market datasets are copied. No Python dependencies or browser secrets are needed.
Docker daemon was unavailable locally, so image build/runtime remains unverified.

Use Liara's Docker platform, repository root build context, Dockerfile at root,
port 8080, TLS reverse proxy, one instance, and a persistent disk mounted at
`/data`. Keep SQLite/raw observer evidence on that disk across restarts. The
image's default command serves existing data only, with no network observer.
To opt into public collection, configure this full process command:

```text
python -B -m wallex.infra.observer_api --host 0.0.0.0 --port 8080 --state-dir /data/observer --frontend-dir /app/frontend/dist --fetch-public --symbol BTCTMN --base BTC --quote TMN --volume-unit base
```

Set the actual Liara application ID/disk in the owner's console before any future
deployment; none is guessed or deployed here. Do not use a static-only Vite host
for the Python worker. No account key belongs in build args, frontend environment
variables or this public container. Authenticated account services are unsupported.
Official configuration references: [Docker deployment](https://docs.liara.ir/paas/docker/how-tos/deploy-app/)
and [persistent disk](https://docs.liara.ir/paas/docker/how-tos/use-disk/).

## Verified local evidence

A real public-only cycle on 2026-10-23 returned HTTP 200 for Wallex M1, native H1
and depth: 264 H1, 24 M15, 4 evaluated steps, 1 technically eligible, 0 replay
errors, NO_TRADE. The actual report reached React in Edge through both Vite proxy
and production HTTP/static serving, with no mocked API and no browser errors.
This is a captured check, not a current trade approval or profitability claim.
Raw public responses and audit state remain outside Git. Private account access
was not performed by this task. See [exact live blockers](AGENT_RUNTIME.md).
