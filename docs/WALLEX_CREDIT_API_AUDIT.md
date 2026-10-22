# Wallex Trade Credit API audit

Audited **2026-10-22** against main
`e4acece392e02cdb774caf0f9d0cd155efe9c6ae`.
Documentation review only: no credentials, private-account requests, activation,
transfers, cancellation or order placement. No algorithms changed or tests run.

**Decision: NO-GO for live credit execution in current QuantForge.** The official
order route and credit-account scoping are documented; implementation, account
provenance, credit risk and reconciliation are not ready. GO for an offline,
read-only credit evidence adapter using supplied sanitized fixtures.

## Evidence and verified capabilities

CONFIRMED = explicitly documented, not verified against a real account.
UNCLEAR = partial/conflicting documentation or semantics requiring confirmation.
NOT DOCUMENTED = absent from the inspected pages/specifications, not proof that
Wallex lacks the capability. All API paths below use `https://api.wallex.ir`;
links point to documentation, not requests executed by this audit.

Current `/docs/` pages and the official downloadable specifications were
retrieved successfully (HTTP 200). The browsing renderer was unreliable for
some pages; their public HTML/specifications were read directly instead.

| Capability | Status | Official method/path and evidence |
| --- | --- | --- |
| Offered plans | CONFIRMED | `GET /v1/prop/packages`; inspect `status=ACTIVE`, currencies, assurance, loan and fees. These are offered plans, not proof the user has an active account. [Plans](https://developers.wallex.ir/docs/trade-credit-active-packages). |
| User credit accounts and active plans | CONFIRMED | `GET /sub-accounts` exposes `clientId`, `userId`, `type`, `isActive`, `isSuspend`; do not accept unrelated account types. `GET /v1/prop/user/packages/overview` maps `subAccountClientId` to user-package status/expiry. `GET /v1/prop/user/packages/active` takes `sub-account-client-id`. [Accounts](https://developers.wallex.ir/docs/generated/active-sub-accounts-list), [overview](https://developers.wallex.ir/docs/generated/active-user-packages-overview), [active detail](https://developers.wallex.ir/docs/generated/active-user-package-details). |
| Credit wallet balances | CONFIRMED | `GET /v1/account/balances-detail` declares `sub-account-client-id` in the Trade Credit OpenAPI and exposes per-currency `total`, `freeze`, `available`. The prose wallet page has a different aggregate shape; do not merge schemas or use aggregate worth as free funds. [Balance operation](https://developers.wallex.ir/docs/generated/sub-account-balances-details), [wallet narrative](https://developers.wallex.ir/docs/trade-credit-wallet). |
| Collateral, debt, fees and expiry fields | CONFIRMED | `GET /v1/prop/user/packages/active` includes assurance, loan, daily fees/discounts, payable fee, payback, status and `expireAt`. `GET /v1/prop/user/packages/history` records closed-plan results. [Active detail](https://developers.wallex.ir/docs/generated/active-user-package-details), [history](https://developers.wallex.ir/docs/trade-credit-sub-account-history). |
| Expiry and automatic closure | CONFIRMED | The terms describe 30-day plans, automatic closure at expiry/liquidation, daily credit fees, renewal and optional automatic wallet funding. Inspect actual returned expiry rather than hardcoding 30 days. [Terms](https://developers.wallex.ir/docs/trade-credit-types). No renewal, wallet connection or plan activation was performed. |
| Warning/liquidation thresholds | CONFIRMED | `GET /v1/prop/packages/{id}` exposes `warning_limit`, `liquidity_limit`; active-account detail exposes `warningLimit`, `liquidityLimit`. Presence is confirmed; comparison units/basis remain UNCLEAR below. [Package detail](https://developers.wallex.ir/docs/trade-credit-package-details), [active detail](https://developers.wallex.ir/docs/generated/active-user-package-details). |
| Eligible markets | CONFIRMED / UNCLEAR | `GET /v1/prop/packages/{id}` supplies `unavailableMarkets`; the shared market inventory is `GET /hector/web/v1/markets`. The mechanism is documented; no current eligible symbol was verified for any account. A blacklist alone does not prove eligibility or instrument compliance. [Package restrictions](https://developers.wallex.ir/docs/trade-credit-package-details), [official order API specification](https://developers.wallex.ir/assets/files/spot-b9d06896d1009adc9ebc69b9a5495e7e.json). |
| CREDIT-scoped order creation | CONFIRMED | `POST /v1/account/orders`, **only with that credit sub-account's `x-api-key` AND `sub-account-client-id` headers**. This is explicitly stated by the chapter introduction, not inferred from the path. Body: `price`, `quantity`, `side`, `symbol`, `type`; optional unique order `client_id`; conditional `stop_Price`. Documented types: LIMIT, MARKET, STOP_LIMIT, STOP_MARKET; creation returns 201. [Explicit credit-account scope](https://developers.wallex.ir/docs/spot-intro), [creation contract](https://developers.wallex.ir/docs/spot-create-order). |
| Credit order lookup, cancellation and lists | CONFIRMED | Under the same explicit credit-key/account-header scope: `GET /v1/account/orders/{client_id}`, `DELETE /v1/account/orders/{client_id}`, `GET /v1/account/orders`, `GET /v1/account/openOrders`. Here the path ID is an ORDER client ID. Lists document pagination; bounded complete traversal is required before asserting flatness. [Scope](https://developers.wallex.ir/docs/spot-intro), [lookup](https://developers.wallex.ir/docs/spot-single-order-detail), [cancel](https://developers.wallex.ir/docs/spot-cancel-order), [history](https://developers.wallex.ir/docs/spot-order-list), [open orders](https://developers.wallex.ir/docs/spot-active-orders). |
| Fill and balance observations | CONFIRMED | Order responses contain `executedQty`, `fills[]`, prices, quantities, fee/fee asset and timestamps; balances use the credit wallet operation above. These are inputs for reconciliation, not proof of atomic or complete account reconciliation. [Order detail](https://developers.wallex.ir/docs/spot-single-order-detail). |
| Permissions and limits | CONFIRMED / UNCLEAR | Keys are account-specific, have selected expiry, read permission by default, and a separate trading permission. Docs state a maximum of three API keys, but its per-user/per-account scope is UNCLEAR. Creation documents **20 requests per 10 seconds**; quota aggregation and other endpoint windows are UNCLEAR. [Key permissions](https://developers.wallex.ir/docs/create-api-key), [order limit](https://developers.wallex.ir/docs/spot-create-order). |

The `/v1/account/orders` family is a shared matching-engine API; its use here
is for a **Trade Credit sub-account**, never a default main-account Spot request.
No Margin endpoint is substituted. Credit management Swagger alone contains no
order-creation operation; the explicit cross-reference above supplies the scope.

## Critical unresolved capabilities

| Status | Blocker and required answer |
| --- | --- |
| UNCLEAR | Management credential/header matrix: balance and active-plan OpenAPI define the account header without `required: true`, while some generated prose says no custom header. Trading prose explicitly requires BOTH headers. Confirm parent-vs-child key access for listing/management, ownership verification, and behavior on absent/mismatched account headers. Never fall back to the main account. |
| UNCLEAR | Exact fee-accrual/debit timing, payable-fee currency, credit exposure versus owned equity, threshold valuation/basis/units, and treatment of locked funds/transfers. Account `available` is not automatically risk equity; `warningLimit` is not a per-instrument stop price. Confirm TMN/IRR/USDT units without implicit conversion. |
| NOT DOCUMENTED | A stable unique venue fill ID or documented equivalent suitable for durable fill deduplication is absent from inspected order responses/specification. A dedicated credit fill-event stream and complete fill-history guarantees were not documented. Do not invent a venue fill ID from price/quantity/time or treat those values as unique. |
| NOT DOCUMENTED | Atomic order/fill/balance snapshot semantics, settlement completeness and guaranteed handling/order prices for outstanding or partially filled orders during expiry, liquidation or account suspension. Automatic closure is documented, its execution/reconciliation protocol is not. |
| UNCLEAR | Client-order-ID uniqueness scope/retention, timeout retry/idempotency guarantees and authoritative not-found semantics. Rate header examples (e.g. 600 or 20) are not normative limits/windows; per-key/IP/account sharing, lookup/cancel budgets and 429 recovery remain unresolved. |

## Account identity boundary

Keep a validated explicit binding of venue, account kind `TRADE_CREDIT`,
sub-account `clientId`/`subAccountClientId`, active user-package ID, offered
package ID, owner identity, currencies, account status/suspension, expiry and a
logical credential reference. Raw API keys must not enter audit events or this
report. Main-account and each credit account remain distinct.

`client_id` in an order body, `clientOrderId` in its response and the order path
ID are **order identities**, not the sub-account header or package ID. Existing
`TradeIntent.account_id` needs an agreed mapping to the credit account;
`TradeIntent.client_id` must remain the stable order identity. Preserve source
and observation timestamps and explicit uncertainty; do not infer freshness.

## Reuse and required changes by module

| Module | Reuse unchanged | Required before credit execution |
| --- | --- | --- |
| Market | Decimal normalization, closed-candle checks, v1 availability/quality/hash helpers. | Verified provider payload, source/receive times, currency/instrument rules and package-market eligibility adapter. Draft COMPLETE quality is not authorization. |
| Strategy | EMA/ATR, Trend/Pullback, proposed stops and v0/v1 diagnostic adapter. | Supply independently validated maximum BUY price Pe and market evidence; do not infer Pe from close/ask or enable executable signals. |
| Risk | Conservative arithmetic and local sizing/loss/portfolio diagnostic primitives. | Credit-specific account/net-equity/debt/fee policy, expiry/liquidation buffers, authoritative funds/portfolio observations and persistent breach recovery. Loss state lacks account identity; generic L0/R0 calculations are not an approved credit policy. |
| Audit | Durable intent identity, journal, account writer and explicit reservations. | Agreed credit account namespace, verified decision/policy references, scoped reserve/capacity, account-state correlation and authoritative atomic admission. Existing ledger does not automatically enforce all credit predicates. |
| Execution | Request/receipt models and replay-tested UNKNOWN recovery/cancellation patterns. | A separately reviewed credit-scoped transport, permissions, status mapping, stable fills and settlement/balance reconciliation, time conversion and instrument rules. Existing AccountSnapshot cannot carry verified balances; ReplayGateway rejects LIVE. Replay is not a real venue adapter. |

## Next task and manual requirements

**CREDIT-02: offline CreditAccountSnapshot diagnostic adapter.** Consume supplied,
sanitized account-list, active-plan and balance responses; validate explicit
sub-account/package/quote identity, status, expiry, units and observation times;
retain collateral/debt/fee/threshold evidence and block incomplete or ambiguous
state. Keep execution disabled; add focused fixture tests. Do not add HTTP writes
or alter trading algorithms in that task.

Before a future live stage, obtain Wallex clarification on the unresolved rows
and agree the account/credit-risk/fill contracts with module owners. An operator
must later confirm the selected existing credit account and its matching key
permissions through an approved process. No credentials are requested here and
no plan activation or funding is authorized by this audit.

Official specification snapshots inspected (public documentation only):

- [Trade Credit OpenAPI](https://developers.wallex.ir/assets/files/trade-credit-ff5c60b5b97b77a5b2c2e4800f07729c.yaml): SHA-256 `d8141055d11f6fa63867f70d3d0449af2bf7c5d86f67eab7f0d37e73ba3395eb`.
- [Shared order API Swagger](https://developers.wallex.ir/assets/files/spot-b9d06896d1009adc9ebc69b9a5495e7e.json): SHA-256 `10d8667602b3f82428b7a35688174449843e6ed36c9de816050dccc3e6a69753`.

## Authentication follow-up ? 2026-10-23

Owner-reported: `GET /sub-accounts` returns 200; `GET
/v1/prop/user/packages/active` returns 401. No private request was made for this
review. The public OpenAPI was retrieved again; its SHA-256 matches the snapshot
above.

- CONFIRMED: both operations declare `security: [{x-api-key: []}]`; the scheme
  is the `x-api-key` header. [Official OpenAPI](https://developers.wallex.ir/assets/files/trade-credit-ff5c60b5b97b77a5b2c2e4800f07729c.yaml).
- CONFIRMED: [account discovery](https://developers.wallex.ir/docs/generated/active-sub-accounts-list)
  declares no endpoint-specific custom header; [active-plan detail](https://developers.wallex.ir/docs/generated/active-user-package-details)
  documents `sub-account-client-id` (the parameter lacks `required: true`). The
  current GET-only client already sends both headers with the same supplied key
  and selected account; no documented extra authentication header was found.
- UNCLEAR: whether these management operations accept the same Trade Credit key
  and read permission. [Credit-key guidance](https://developers.wallex.ir/docs/spot-intro)
  explicitly covers trading, not a management-endpoint access matrix. Discovery
  success does not establish active-plan authorization; the 401 cause is unverified.

**Blocker:** obtain official Wallex confirmation of API-key account scope,
read-permission eligibility and required headers for the active-plan operation.
Credit-plan collection remains unavailable on 401: no partial evidence bundle,
credential fallback, cookies, session tokens or authentication bypass. No code
changed; `NO_TRADE` and `executable_ready=False` remain enforced.
