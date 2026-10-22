"""Offline replay gateway. Stable identities, uncertainty and reconciliation only."""

from dataclasses import dataclass

from wallex.audit.ledger import Ledger, ObservationError
from wallex.risk import RiskReason
from wallex.strategy import Signal, StrategyDecision

from .models import ContractError, ExecutionReceipt, ExecutionRequest, Mode, OrderState, json_value, utc
from .replay import ReplayTransport


class ExecutionRefused(RuntimeError):
    pass


class LiveUnavailable(ExecutionRefused):
    pass


class RecoveryRequired(ExecutionRefused):
    pass


_UNRESOLVED = {OrderState.PLANNED, OrderState.SUBMITTING, OrderState.UNKNOWN,
               OrderState.RECONCILING, OrderState.CANCEL_PENDING}


@dataclass(frozen=True)
class GatewayResult:
    disposition: str
    receipt: ExecutionReceipt | None = None
    audit_event_id: str | None = None

    def to_dict(self) -> dict:
        return json_value(self)


class ReplayGateway:
    def __init__(self, ledger: Ledger, transport: ReplayTransport, *, clock):
        if not isinstance(ledger, Ledger) or not isinstance(transport, ReplayTransport) or not callable(clock):
            raise TypeError("Install the local Ledger, synthetic ReplayTransport and an explicit clock.")
        self.ledger, self.transport, self.clock = ledger, transport, clock
        self.stop_new_entries = False
        self._recovered_accounts: set[str] = set()

    def _now(self):
        return utc(self.clock())

    def _uncertain(self, record, reason: str) -> ExecutionReceipt:
        self.ledger.transition(record.request.intent.intent_id, OrderState.UNKNOWN,
                               expected_version=record.version, at=self._now(), reason=reason)
        return self.ledger.receipt(record.request.intent.intent_id)

    def _accept(self, record, observation) -> ExecutionReceipt:
        try:
            self.ledger.observe(record.request.intent.intent_id, observation,
                                expected_version=record.version, at=self._now())
        except (ObservationError, ContractError, ValueError, TypeError):
            return self._uncertain(record, "invalid_or_incomplete_source_evidence")
        # Storage failures propagate: the last committed control state remains
        # recoverable. Never repeat transport I/O to recover an audit commit.
        return self.ledger.receipt(record.request.intent.intent_id)

    def submit(self, request: ExecutionRequest) -> GatewayResult:
        if not isinstance(request, ExecutionRequest):
            raise ContractError("Expected the exact typed offline execution request.")
        request = ExecutionRequest.from_dict(request.to_dict())
        intent = request.intent
        if intent.mode in {Mode.LIVE, Mode.SHADOW}:
            kind = "live_refused" if intent.mode == Mode.LIVE else "shadow_decision_recorded"
            event = self.ledger.record_event(kind, account_id=intent.account_id, run_id=intent.run_id,
                                             trace_id=intent.trace_id, at=self._now(),
                                             payload={"request": request, "fingerprint": request.fingerprint})
            if intent.mode == Mode.LIVE:
                raise LiveUnavailable("Live trading is unavailable; no order was submitted.")
            return GatewayResult("shadow_recorded", audit_event_id=event)
        if (request.risk_decision.allowed is not True or request.risk_decision.reason != RiskReason.WITHIN_LIMITS):
            raise ExecutionRefused("The actual provisional risk check did not pass.")
        if intent.purpose != "entry" or intent.side.value != "BUY":
            raise ExecutionRefused("Risk-reducing exits and sell execution require a later reviewed stage.")
        with self.ledger.writer(intent.account_id):
            existing = self.ledger.existing(request)
            if existing is not None:
                disposition = ("existing_no_resubmission" if intent.account_id in self._recovered_accounts
                               else "reconciliation_required")
                return GatewayResult(disposition, self.ledger.receipt(intent.intent_id))
            records = self.ledger.records(intent.account_id)
            if records and intent.account_id not in self._recovered_accounts:
                raise RecoveryRequired("Reconcile persisted account intents before new execution after restart.")
            if self.stop_new_entries:
                raise ExecutionRefused("New entries are paused; reconciliation and cancellation remain available.")
            if any(record.reservation_held or record.state in _UNRESOLVED for record in records):
                raise ExecutionRefused("The account has held exposure or uncertainty; no new entry is safe.")
            if self._now() < intent.created_at:
                raise ExecutionRefused("Intent creation is in the future; no submission is permitted.")
            record, created = self.ledger.plan(request, at=self._now())
            if not created:
                return GatewayResult("existing_no_resubmission", self.ledger.receipt(intent.intent_id))
            self._recovered_accounts.add(intent.account_id)
            if self._now() >= intent.expires_at:
                self.ledger.reject_unsubmitted(intent.intent_id, expected_version=record.version,
                                               at=self._now(), reason="expired_before_submission")
                return GatewayResult("expired_without_submission", self.ledger.receipt(intent.intent_id))
            record = self.ledger.transition(intent.intent_id, OrderState.SUBMITTING,
                                            expected_version=record.version, at=self._now(),
                                            reason="submission_acceptance_unconfirmed")
            try:
                observation = self.transport.submit(intent)
            except Exception:
                return GatewayResult("submission_unknown", self._uncertain(record, "submission_outcome_unknown"))
            return GatewayResult("submission_observed", self._accept(record, observation))

    def _reconcile_locked(self, record) -> ExecutionReceipt:
        intent = record.request.intent
        if not record.reservation_held and record.state == OrderState.REJECTED:
            return self.ledger.receipt(intent.intent_id)
        record = self.ledger.transition(intent.intent_id, OrderState.RECONCILING,
                                        expected_version=record.version, at=self._now(),
                                        reason="source_lookup_in_progress")
        try:
            observation = self.transport.lookup(intent.account_id, intent.client_id)
        except Exception:
            return self._uncertain(record, "reconciliation_source_unavailable")
        if observation is None:
            return self._uncertain(record, "not_found_does_not_prove_nonacceptance")
        return self._accept(record, observation)

    def reconcile(self, intent_id: str) -> ExecutionReceipt:
        record = self.ledger.get(intent_id)
        if record is None:
            raise ExecutionRefused("Unknown intent; no stable identity is available to reconcile.")
        with self.ledger.writer(record.request.intent.account_id):
            return self._reconcile_locked(self.ledger.get(intent_id))

    def recover_account(self, account_id: str, *, run_id: str, trace_id: str) -> tuple[ExecutionReceipt, ...]:
        with self.ledger.writer(account_id):
            receipts = tuple(self._reconcile_locked(record) for record in self.ledger.records(account_id))
            unresolved = any(receipt.state in _UNRESOLVED for receipt in receipts)
            self.ledger.record_event("account_recovery_blocked" if unresolved else "account_order_recovery_completed",
                                     account_id=account_id, run_id=run_id, trace_id=trace_id, at=self._now(),
                                     payload={"scope": "synthetic_order_only", "account_balances_reconciled": False})
            if not unresolved:
                self._recovered_accounts.add(account_id)
            else:
                self._recovered_accounts.discard(account_id)
            return receipts

    def cancel(self, intent_id: str) -> ExecutionReceipt:
        record = self.ledger.get(intent_id)
        if record is None:
            raise ExecutionRefused("Unknown intent.")
        intent = record.request.intent
        with self.ledger.writer(intent.account_id):
            if intent.account_id not in self._recovered_accounts:
                raise RecoveryRequired("Reconcile orders after restart before requesting cancellation.")
            record = self.ledger.get(intent_id)
            if record.state not in {OrderState.WORKING, OrderState.PARTIAL}:
                raise RecoveryRequired("Cancellation state is uncertain or terminal; reconcile instead of retrying.")
            record = self.ledger.transition(intent_id, OrderState.CANCEL_PENDING,
                                            expected_version=record.version, at=self._now(),
                                            reason="cancellation_unconfirmed")
            try:
                observation = self.transport.cancel(intent.account_id, intent.client_id)
            except Exception:
                return self._uncertain(record, "cancellation_outcome_unknown")
            return self._accept(record, observation)

    def consume_strategy_decision(self, decision: StrategyDecision, *, account_id: str,
                                  run_id: str, trace_id: str) -> GatewayResult:
        if not isinstance(decision, StrategyDecision) or decision.signal != Signal.NO_TRADE:
            raise ExecutionRefused("Only the existing Strategy NO_TRADE decision can be consumed here.")
        event = self.ledger.record_event("strategy_no_trade", account_id=account_id, run_id=run_id,
                                         trace_id=trace_id, at=self._now(), payload={"decision": decision})
        return GatewayResult("no_trade", audit_event_id=event)
