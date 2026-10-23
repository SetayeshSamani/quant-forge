# Offline TP-1 integration

Run from the repository root; no network, credentials or account data are used:

```powershell
python -B -m wallex.shadow.offline_tp1 --analyze --m1-input SAVED_M1_UDF_JSON --h1-input SAVED_NATIVE_H1_UDF_JSON --symbol BTCUSDT --base BTC --quote USDT --m1-start M1_START --m1-end M1_END --h1-start H1_START --h1-end H1_END --as-of CUTOFF --evidence-kind historical_data --volume-unit base
```

Replace every placeholder with the **actual saved request** metadata. Supported
real-data inputs are two raw public Wallex UDF response JSON files: M1 and native
`resolution=60` H1. This command does not accept a collection directory, manifest,
JSONL dataset or several concatenated JSON responses. Each input is limited to
1 MiB and 14 days. All timestamps are integer UTC Unix **open** seconds; ranges
are inclusive-start/exclusive-end and both ends must be closed by `CUTOFF`.
M1 bounds must be minute-aligned, H1 bounds hour-aligned. Partial M15 edge buckets
are reported and excluded. Raw bodies, not filenames or requested resolution,
are checked for actual spacing/coverage, OHLCV, identity echoes and completion.
Hashes preserve byte identity, **not** authenticity. Native resolution/source
identity are caller claims. Input files are unchanged; derived JSONL is strictly
loaded in temporary storage and removed automatically. No downloaded data belongs
in Git. Use `--evidence-kind synthetic_fixture` for synthetic inputs.

`--volume-unit base` is an explicit **unverified caller assumption**, never an
independent unit verification. Omit it to analyze coverage with unknown units;
model construction/technical evaluation stays blocked. Native volume is never
converted. Cross-timeframe price equivalence is not independently established.

Each M15 segment starts its own warm-up: at least 21 consecutive bars, plus 250
consecutive H1 bars **closed at that step's cutoff**. H1 observations after that
cutoff are excluded. The known ten-hour sample's **two separate 19-bar segments
are insufficient**; they never become a 38-bar continuous history. Earlier H1
history may also be needed even when the full H1 file contains 250 or more bars.
Stale H1 prefixes fail existing ReplayPlan/MarketFrame checks. No bar is invented,
forward-filled or bridged across gaps; strict M1 ingestion is unchanged.

Stdout is deterministic JSON: source/derived hashes, ranges, gap findings,
per-segment insufficient counts, evaluated Shadow steps, actual technical/risk/
credit reasons and aggregate blockers. Missing histories are counted before
technical evaluation; malformed input produces `INPUT_REJECTED` JSON and exit 1.
Evaluable steps reuse `run_tp1_shadow` (including existing risk/credit diagnostics).
Pe, balances, equity, fees, credit and account identity stay UNVERIFIED: no risk
inputs are supplied and no equity is inferred. Quote/depth/freshness and execution
permission remain unavailable. Stops/sizing/approval are not manufactured.
Every result is **NO_TRADE**, `executable_ready=false`, without order quantities.
This workflow is research diagnostics, not an executable strategy or backtest.
