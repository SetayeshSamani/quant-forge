# Offline execution consumer foundation

Scope: `wallex/execution/` and `wallex/audit/` only. The agreed design direction
was read in full from `QuantForge_Project_Plan.md` (236 lines), SHA-256
`56a2c3d370ab2fffe6908139265334eea21d76f4faaea3efe9f56a93017ac7df`.
Its design direction does not authorize live trading or select account settings.

The verified base is actual main
`de3051be409e2dfb3530e556cd6205f201b15578`. Fetch/prune/tags and main's tracked
fast-forward pull completed. This clone had no previous local implementation or
unpublished commits; no personal branch names required migration. The existing
TP-1 indicator PR #2 remains separate and unmodified. No applicable AGENTS.md or
shared schema module was found. The 25 existing strategy/risk/runtime tests pass.

## Provisional boundary

`models.py` consumes the actual `wallex.risk.RiskDecision`. Its `allowed` flag
only represents the existing quantity/notional check and is never full execution
authorization. No finalized shared TradeIntent, ExecutionReceipt, Fill,
AccountSnapshot or AuditEvent schema exists in the fetched histories. These
small module-local consumers require coordination with Setayesh and Roham.

`ExecutionRequest` requires a stable producer-owned intent/client ID, exact
account/instrument/assets, side, explicit LIMIT price/quantity and deadline,
mode, run/trace IDs, decision/version/account references and a supplied
reservation/capacity limit. No IDs, volume, markets, risk profile, fee setting,
tick/step, minimum size or settlement deadline is inferred. Reservation is
validated, never resized: buys reserve quote units covering the explicit gross
order; sells reserve base units. Fee sufficiency and full risk remain upstream
responsibilities. Capacity is an explicit fixture ledger limit, not proof of a
reconciled account balance.

Money and quantity reject floats, integers, numeric strings and booleans at
typed boundaries; callers provide Decimal. Storage serialization uses canonical
decimal strings, aware timestamps normalized to UTC, schema version and
correlation references. A 200-digit/absolute decimal-order bound is a storage
representability guard, not an exchange quantity/price/risk limit. Exact
arithmetic does not depend on Decimal's default 28-digit context.

Unknown fees, source times and Agent/account evidence retain null plus a reason.
`AccountSnapshot` is separate from order receipts and currently represents only
unavailable account balances. It does not infer that an order or account is flat.
Fill records preserve source identity, price, base quantity, fee asset/amount and
timestamps. No real balance collector, exchange adapter or settlement is present.

Modes use `replay`, `shadow`, `live`. There is no earlier `paper` implementation
to map. Synthetic fixtures call the actual risk function with explicitly labeled
fixture settings; they are not signals produced by Strategy, which still returns
NO_TRADE. No TP-1 calculations, market/backtest/shadow runner or AI tools are added.

Use the existing standard-library environment from the repository root:

```powershell
python -B -m unittest discover -s wallex -p 'test_*.py' -v
```

The next commits add the durable ledger and injected replay gateway. Live remains
unavailable; no REST/MCP order submission, credentials or funded account is used.
