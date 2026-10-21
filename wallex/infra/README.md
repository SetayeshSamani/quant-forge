# Runtime foundation

Purpose: a minimal immutable runtime state model. It runs no trading loop,
schedules no work, and creates no threads or processes.

Ownership: Setayesh owns this directory, `wallex/strategy/`, and
`wallex/risk/` only. This package does not implement teammate-owned shared
contracts, market, backtest, shadow, execution, or audit modules.

Provisional interfaces: `RuntimeState` supports `STOPPED`, `STARTING`,
`RUNNING`, `DEGRADED`, and `STOPPING`. `Runtime()` defaults to `STOPPED`.
`transition(target)` returns a new immutable model and rejects invalid
transitions. State labels do not activate services. Direct construction at
another valid state is possible for modeling, with live execution still off.

```text
STOPPED  -> STARTING
STARTING -> RUNNING | DEGRADED | STOPPING
RUNNING  -> DEGRADED | STOPPING
DEGRADED -> RUNNING | STOPPING
STOPPING -> STOPPED
```

`live_trading_enabled` is always false, has no configuration switch, and
cannot be enabled through the interface. `execute_order(payload)` always
raises `LiveTradingDisabledError` in every state, without inspecting or
forwarding the payload. No exchange client or executor is present.

Dependencies awaiting teammates: approved lifecycle and health contracts,
startup/shutdown policy, market readiness and freshness, risk readiness,
execution interfaces, and audit/event schema. This internal model is not
a shared contract or production supervisor.

Safety limitations: no background trading, network calls, environment or
credential loading, real orders, or automatic recovery. No persistence,
synchronization, resource management, or exchange integration is implemented.
A future live mode requires separate explicit team-approved implementation;
moving to `RUNNING` never enables it in this foundation.

Planned integration: map team health/readiness events into lifecycle
transitions and expose state to future monitoring/audit adapters. Wire
strategy and risk only after their approved contracts exist. Teammate-owned
execution remains separate; there is no execution callback or adapter here.

Dependencies: Python 3.11 standard library only; no third-party packages.
From the repository root, test without bytecode files:

```text
python -B -m unittest discover -s wallex -p test_runtime.py -v
```
