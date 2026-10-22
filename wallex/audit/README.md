# Durable Intent / Audit ledger

`Ledger(path)` uses a dedicated local SQLite database with full-sync commits.
It refuses unrelated databases and unknown versions without replacing their
contents. Schema version is `audit-ledger-v1`; monetary columns contain canonical
decimal text, not SQLite REAL values. SQL SUM/coercion is not used for money.

`plan(request, at=...)` atomically commits the complete request and its fingerprint,
stable intent/client identity, exact account/instrument/mode, decision/version
references, explicit reservation, PLANNED state and initial AuditEvent. Identical
requests return the existing record; conflicting reuse of either identity raises
`IdentityConflict`. Client IDs are unique per account across persisted intents;
intent IDs are unique across this ledger. The consumer never regenerates either.

The reservation/capacity is supplied explicitly by the validated request, not
calculated from an assumed account balance or risk profile. `reserved(account,
asset)` accounts for every held reservation. A short `BEGIN IMMEDIATE` transaction
serializes aggregate-capacity checks and insertion. Failed audits roll back state
and reservation changes. Missing/invalid risk results cannot admit replay orders;
the existing RiskDecision remains a partial check, not full authorization.

`record_event` / `events` expose an append-only API with stable persisted event
IDs, run/trace identifiers, schema version and UTC time. The SQLite file remains
trusted local storage, not a tamper-proof or cryptographically signed audit log.
No secret, transport exception text or credential is collected by the gateway.

`ledger.writer(account)` takes a nonblocking OS lock outside database transactions.
Separate local gateway instances/processes using the same canonical DB path
cannot hold the same account writer concurrently. Other accounts have separate
OS locks; SQLite still serializes their short commits. Kernel locks release on
crash, while durable SUBMITTING/UNKNOWN records survive for reconciliation.
There is no lease expiration that permits a second live writer during slow I/O.
This is single-host coordination, not distributed exactly-once execution. Network
filesystems and hard-link aliases are unsupported; all processes must use the
same resolved ledger path and writer protocol.

Control transitions require an exact current record version and commit their
audit event atomically:

- PLANNED -> SUBMITTING before a first transport call.
- WORKING/PARTIAL -> CANCEL_PENDING before a future replay cancellation call.
- Existing order states -> RECONCILING before a source lookup.
- SUBMITTING/CANCEL_PENDING/RECONCILING -> UNKNOWN on uncertainty.
- PLANNED -> REJECTED may release a reservation only when submission never began.

Receipt-driven WORKING/PARTIAL/FILLED/CANCELED/REJECTED transitions require
validated replay observations, not a generic state assignment. Source references,
monotonic cumulative quantities and complete deduplicated fill details are checked
in the same transaction as fill insertion, state and audit. Invalid or incomplete
evidence leaves an UNKNOWN receipt, preserving the unusable typed evidence in the
audit for investigation. Stored request fingerprints and identity columns are
rechecked before recovery can use a client ID.
Externally observed terminal states retain reservations in this stage:
an order receipt is not an account settlement proof. No transaction remains open
across external I/O. A failure after transport acceptance cannot undo the venue;
the last durable uncertain state must be reconciled instead.

Original source base: main `de3051be409e2dfb3530e556cd6205f201b15578`.
`feat/audit-intent-ledger` contains the provisional consumers, durable admission
and writer/recovery controls. `feat/execution-replay-reconciliation` stacks
on its completed tip `8814b4ea230d314cc3f5f6d442ada7a341ac1a18`.
This task does not merge any branch or PR into main.

Foundation checkpoint on Windows / Python 3.14.8: 42 combined unittest tests, including the
25 untouched strategy/risk/runtime cases. Tests use temporary databases and
explicit synthetic requests. They exercise atomic audit failure, uniqueness,
aggregate reservation competition, version refusal, cross-process writer exclusion
and lock release after a synthetic process death. No live account is accessed.
The completed stacked replay stage passes 68 combined tests; see
[the execution handoff](../execution/HANDOFF.md) for final verification and scope.
It also passes 140 tests when combined, in a detached temporary worktree, with
the subsequently advanced main `dd3b40cf42b5abd58a318551bd05f93bca966205`.

```powershell
python -B -m unittest discover -s wallex -p 'test_*.py' -v
```
