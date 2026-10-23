# Exact release reproduction (no private API, credentials or orders)

Run from the reviewed repository root. Source checkout must be clean. Do not
build from an unreviewed working tree or claim a deployment until verified.

## Pin source and run checks (PowerShell)

```powershell
$sha = git rev-parse HEAD
if (git status --porcelain) { throw "Working tree must be clean" }
$modules = @(rg --files wallex -g 'test_*.py' | Sort-Object | ForEach-Object { ($_ -replace '[\\/]', '.') -replace '\.py$', '' })
python -B -m unittest $modules -q
npm --prefix frontend ci
$env:PW_BROWSER_CHANNEL = 'msedge' # or install/use Playwright Chromium
npm --prefix frontend test
npm --prefix frontend run check
npm --prefix frontend run build
```

## Public worker + API/UI (native or container)

```powershell
$state = "$env:LOCALAPPDATA\QuantForge\release-native-qa\data\observer"
$env:QF_SOURCE_COMMIT = $sha # nonsecret deployment identity, never an API key
python -B -m wallex.infra.observer_api --host 127.0.0.1 --port 8080 --state-dir "$state" --frontend-dir frontend/dist --fetch-public --symbol BTCTMN --base BTC --quote TMN --volume-unit base
```

Only --fetch-public opts into public collection. Base-volume units remain an
explicit unverified assumption. No account connection occurs. Open
http://127.0.0.1:8080 and /api/health, /api/observer. Native port8080 UI/health and
public collection were observed successfully; Ctrl+C recorded STOPPED while
preserving data. Restart with the same directory, never another simultaneous
writer. Check health.source_commit against `$sha`. A snapshot/heartbeat is not
proof of source event freshness or ongoing process liveness.

Container commands below are prepared, **not successfully verified locally**:
Docker daemon is available, but Docker Hub Python layer download returned HTTP403.

```powershell
docker build --build-arg SOURCE_COMMIT=$sha -t quantforge:$sha .
docker volume create quantforge-public-data
docker run --name quantforge-release -p 127.0.0.1:8080:8080 --mount source=quantforge-public-data,target=/data quantforge:$sha --host 0.0.0.0 --port 8080 --state-dir /data/observer --frontend-dir /app/frontend/dist --fetch-public --symbol BTCTMN --base BTC --quote TMN --volume-unit base
# Different terminal, after observing real API/UI results:
docker inspect --format '{{.Config.Labels}} {{.State.Health.Status}}' quantforge-release
docker stop --time 60 quantforge-release
docker start quantforge-release
```

On a future Liara build select Docker, repository-root context, port8080, one
instance, TLS reverse proxy and persistent disk /data. Preserve /data/observer on
restart. Configure the same full Python process command from OBSERVER_DASHBOARD.md.
No app/disk identifier is guessed; owner provisioning and actual deployment remain
manual. Resolve registry access on that host without disabling security.

## Export real public TP-1 evidence (different terminal)

Choose a completed observation directory with report.json, m1.json and h1.json.
Use its recorded cutoff, not the wall clock. The script verifies original raw
hashes, source symbol/ranges and independent consecutive segments. Output must be
a NEW directory outside Git; short segments are reported, never combined.

```powershell
$obs = Get-ChildItem "$state\observations" -Directory | Sort-Object Name -Descending | Select-Object -First 1
$cutoff = (Get-Content "$($obs.FullName)\report.json" -Raw | ConvertFrom-Json).decision_cutoff
$evidence = "$env:LOCALAPPDATA\QuantForge\release-evidence-$sha"
python -B -m wallex.shadow.release_evidence --observation-dir "$($obs.FullName)" --output "$evidence" --symbol BTCTMN --base BTC --quote TMN --as-of $cutoff --source-commit $sha --simulation-cash 100000000 --fee-rate 0.001 --slippage-rate 0.002 --latency-seconds 0
```

Capital 100000000 quote units, fee 0.001 and slippage 0.002 are HYPOTHETICAL
simulation assumptions, never real balances, actual account fees or approved
risk budgets. TP-1/Shadow remain NO_TRADE; zero fills is not evidence of profitable
TP-1 trading. Package includes original public M1/H1 bytes plus a sanitized source descriptor
`public-source/report.json`, verified raw/derived hashes, actual Shadow/backtest
journals, step cutoffs and source commit manifest. The exported public-source
directory alone can be supplied as --observation-dir to reproduce the same hashes.
Depth hash is preserved in public snapshot; no original raw depth bytes are
bundled because the existing worker did not save them. No fabricated hash is added.

Separately demonstrate existing synthetic fee accounting; do not mix its fills or
P&L with the historical TP-1 report:

```powershell
python -B -m wallex.backtest.example --output "$env:LOCALAPPDATA\QuantForge\synthetic-cost-$sha"
```

Expected fixture-only result: two hypothetical fills, fee0.2 + slippage0.4 =
loss0.6 generic QUOTE, matching Shadow decisions. Existing tests independently
check this arithmetic. No take-profit, exit policy or genuine strategy profit is
invented. AI is NOT contributing: existing client gates block unverified risk/
credit; no provider call or credential is required for any command above.

## Source/video/evidence submission

```powershell
$delivery = "$env:LOCALAPPDATA\QuantForge\delivery-$sha"
New-Item -ItemType Directory -Path "$delivery" # NEW path, never overwrite
git archive --format=zip --output="$delivery\quantforge-source-$sha.zip" $sha
Compress-Archive -LiteralPath "$evidence" -DestinationPath "$delivery\quantforge-evidence-$sha.zip"
```

Record the outline in HACKATHON_SUBMISSION.md; attach the reviewed source/evidence
archives and the video in the competition portal before 23:50 Iran time tonight.
If a reviewed merge changes the source SHA, regenerate source manifest/image and
archives for that exact commit. Do not manually label an older build as new code.
