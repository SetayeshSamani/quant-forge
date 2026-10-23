"""Real Wallex gateway on the existing durable state machine; disabled by default.

Trusted local backend must supply current independently verified prerequisites
and an explicit account-bound owner confirmation. Read-only preflight cannot
produce missing permissions, fees, coverage or approved risk provenance.
"""

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import re

from wallex.risk.pipeline import assess_risk_pipeline
from wallex.risk import RiskReason
from .admission import ReplayAdmission
from .gateway import ReplayGateway, ExecutionRefused, GatewayResult, _UNRESOLVED
from .models import Mode, OrderState, Side, amount, canonical_json, exact_sum, utc
from .wallex_http import WallexExchange, BalanceEvidence

REQUIRED_PROOFS = frozenset({"ACCOUNT_SCOPE", "TRADE_PERMISSION", "CREDIT_SPOT_ELIGIBILITY", "FEES",
    "MARKET_RULES", "COMPLETE_ACCOUNT_EXPOSURE", "QUOTE_EVENT_FRESHNESS", "RISK_AUTHORIZATION",
    "EXIT_AND_RECOVERY_POLICY", "TMN_SETTLEMENT_RULES"})


@dataclass(frozen=True)
class LiveEvidence:
    checks: ReplayAdmission
    balances: BalanceEvidence
    proofs: tuple[tuple[str, str], ...]  # Approved evidence byte hashes, NOT a boolean preflight summary.
    origin: str  # LIVE_VERIFIED or HTTP_MOCK. Never silently promote mock evidence.
    min_quantity: Decimal
    max_quantity: Decimal
    fee_asset: str


@dataclass(frozen=True)
class ProtectionPolicy:
    initial_stop: Decimal
    minimum_sell_price: Decimal
    maximum_base_fee_rate: Decimal


@dataclass(frozen=True)
class _WritePermit:
    account_id: str = field(repr=False)
    client_id: str = field(repr=False)
    operation: str
    expires_at: datetime


class WallexGateway(ReplayGateway):
    """Reuses durable plan/submit/reconcile/cancel/recover; no daemon or CLI LIVE flag.

    Explicit authorize_entry/exit/cancel is required in each local process. A
    recovered uncertain intent is looked up by its original ID, NEVER reposted.
    Missing evidence or owner confirmation blocks every actual write.
    """
    transport_type = WallexExchange
    supports_live = True

    def __init__(self, ledger, transport, *, clock):
        if ledger.mode is not Mode.LIVE:
            raise ExecutionRefused("DEDICATED_LIVE_LEDGER_REQUIRED")
        super().__init__(ledger, transport, clock=clock)
        self._approvals, self.last_blockers = {}, ()
        for record in ledger.records(transport.account_id):
            transport.bind(record.request.intent)

    def readiness(self, request, evidence, *, entry=True, settlement=False):
        reasons=[]
        try:
            intent=request.intent
            if (intent.mode is not Mode.LIVE or request.evidence_kind != "live_verified"
                    or intent.account_id != self.transport.account_id):
                reasons.append("LIVE_ACCOUNT_BINDING_MISMATCH")
            if type(evidence) is not LiveEvidence or type(evidence.checks) is not ReplayAdmission:
                raise ValueError()
            if (evidence.origin != ("LIVE_VERIFIED" if self.transport.actual_https else "HTTP_MOCK")):
                reasons.append("MOCK_OR_UNVERIFIED_EVIDENCE_ON_REAL_TRANSPORT")
            proof=dict(evidence.proofs)
            if len(proof)!=len(evidence.proofs) or set(proof)!=REQUIRED_PROOFS or any(
                    type(v) is not str or re.fullmatch(r"[0-9a-f]{64}",v) is None for v in proof.values()):
                reasons.append("MISSING_VERIFIED_ACCOUNT_FEE_LIMIT_RISK_OR_EXIT_PROOFS")
            e,now=evidence.checks,self._now()
            if any(v is not True for v in (e.account_scope_verified,e.trade_permission_verified,e.market_approved,
                    e.event_time_verified,e.order_coverage_complete)):
                reasons.append("UNVERIFIED_SCOPE_PERMISSION_MARKET_FRESHNESS_OR_EXPOSURE")
            if (intent.symbol,intent.base_asset,intent.quote_asset)!=(e.market_symbol,e.base_asset,e.quote_asset):
                reasons.append("MARKET_IDENTITY_MISMATCH")
            if (type(e.max_age_seconds) is not int or not 0<=e.max_age_seconds<=30
                    or not 0<=(now-utc(e.observed_at)).total_seconds()<=e.max_age_seconds):
                reasons.append("QUOTE_STALE_OR_FUTURE")
            if (type(evidence.balances) is not BalanceEvidence or evidence.balances.account_id!=intent.account_id
                    or not 0<=(now-utc(evidence.balances.observed_at)).total_seconds()<=30):
                reasons.append("ACCOUNT_BALANCE_SCOPE_OR_FRESHNESS")
            for v in (e.bid,e.ask,e.price_step,e.quantity_step,e.min_notional,e.max_notional,evidence.min_quantity,evidence.max_quantity):amount(v)
            if not evidence.min_quantity<=intent.quantity_base<=evidence.max_quantity or evidence.fee_asset not in (intent.base_asset,intent.quote_asset):
                reasons.append("QUANTITY_LIMIT_OR_FEE_CURRENCY_UNVERIFIED")
            amount(e.fee_quote_rate,zero=True)
            if e.bid>=e.ask or e.fee_quote_rate>=1 or e.min_notional>e.max_notional:
                raise ValueError()
            price,quantity=Fraction(intent.price_quote_per_base),Fraction(intent.quantity_base)
            if ((price/Fraction(e.price_step)).denominator!=1 or (quantity/Fraction(e.quantity_step)).denominator!=1
                    or not Fraction(e.min_notional)<=price*quantity<=Fraction(e.max_notional)):
                reasons.append("PRICE_QUANTITY_INCREMENT_OR_NOTIONAL_LIMIT")
            if type(e.opposing_sell_prices) is not tuple or len(e.opposing_sell_prices)>100:
                raise ValueError()
            for p in e.opposing_sell_prices:amount(p)
            # Entries cross no own sells. Exits require a complete flat opposite-order snapshot below.
            if entry and any(Fraction(p)<=price for p in e.opposing_sell_prices):
                reasons.append("ANTI_SELF_TRADE_REJECTED")
            if request.risk_decision.allowed is not True or request.risk_decision.reason is not RiskReason.WITHIN_LIMITS:
                reasons.append("RISK_DECISION_NOT_APPROVED")
            if entry:
                r=e.risk_inputs
                assessed=assess_risk_pipeline(r)
                if self.transport.actual_https and not assessed.executable_ready:
                    reasons.append("RISK_PIPELINE_DIAGNOSTIC_ONLY_NOT_LIVE_AUTHORITY")
                if not assessed.local_conditions_satisfied or assessed.sizing is None or not assessed.sizing.calculation_valid:
                    reasons.append("CURRENT_RISK_PIPELINE_REJECTED")
                elif quantity>Fraction(assessed.sizing.theoretical_quantity_upper_bound):
                    reasons.append("RISK_SIZE_EXCEEDED")
                if (r.account_id!=intent.account_id or r.quote_asset!=intent.quote_asset or r.frame.symbol!=intent.symbol
                        or not 0<=int(now.timestamp())-r.frame.as_of<=30
                        or price>Fraction(r.pe) or Fraction(e.ask)>Fraction(r.pe)):
                    reasons.append("RISK_ACCOUNT_CUTOFF_OR_PE_MISMATCH")
                available=evidence.balances.row(intent.quote_asset)[2]
                funded=price*quantity*(1+Fraction(e.fee_quote_rate))
                if (funded>Fraction(available) or funded>Fraction(r.free_quote)
                        or funded>Fraction(request.reservation.amount)
                        or request.reservation.capacity_limit>available):
                    reasons.append("INSUFFICIENT_UPFRONT_FEE_FUNDS_OR_CAPITAL")
                if evidence.balances.row(intent.base_asset)[1]!=0:
                    reasons.append("PREEXISTING_BASE_POSITION")
                if any(row[1]>0 for row in evidence.balances.assets if row[0] not in (intent.base_asset,intent.quote_asset)):
                    reasons.append("OTHER_ASSET_EXPOSURE")
            else:
                if not settlement and intent.quantity_base>evidence.balances.row(intent.base_asset)[2]:
                    reasons.append("INSUFFICIENT_FREE_BASE")
        except Exception:
            reasons.append("INVALID_OR_INCOMPLETE_LIVE_EVIDENCE")
        return tuple(dict.fromkeys(reasons))

    def _owner(self, confirmation):
        if confirmation != "AUTHORIZE WALLEX LIVE "+self.transport.account_id:
            raise ExecutionRefused("EXPLICIT_LOCAL_OWNER_ACTIVATION_REQUIRED")

    def authorize_entry(self, request, evidence, protection, *, owner_confirmation=None):
        reasons=self.readiness(request,evidence)
        self.last_blockers=reasons
        if reasons:raise ExecutionRefused(",".join(reasons))
        self._owner(owner_confirmation)
        self.transport.verify_scope()
        if (type(protection) is not ProtectionPolicy or request.intent.side is not Side.BUY
                or request.intent.purpose!="entry"):
            raise ExecutionRefused("VALID_PROTECTIVE_EXIT_POLICY_REQUIRED")
        for value in (protection.initial_stop,protection.minimum_sell_price):amount(value)
        amount(protection.maximum_base_fee_rate,zero=True)
        if not 0<protection.minimum_sell_price<=protection.initial_stop<request.intent.price_quote_per_base or protection.maximum_base_fee_rate>=1:
            raise ExecutionRefused("INVALID_STOP_OR_EXIT_LIMIT")
        assessed=assess_risk_pipeline(evidence.checks.risk_inputs)
        if assessed.frame is None or assessed.frame.stop is None or protection.initial_stop!=assessed.frame.stop.initial_stop:
            raise ExecutionRefused("PROTECTIVE_STOP_MUST_MATCH_APPROVED_RISK_INPUTS")
        risk=evidence.checks.risk_inputs
        bound=(Fraction(protection.initial_stop)-Fraction(protection.minimum_sell_price)
               +(Fraction(risk.pe)+Fraction(protection.minimum_sell_price))*Fraction(evidence.checks.fee_quote_rate))
        if Fraction(risk.worst_case_extra_loss_per_unit)<bound:
            raise ExecutionRefused("WORST_CASE_RISK_COST_DOES_NOT_COVER_EXIT_FLOOR_AND_FEES")
        if evidence.fee_asset==request.intent.base_asset and protection.maximum_base_fee_rate<evidence.checks.fee_quote_rate:
            raise ExecutionRefused("EXIT_BASE_FEE_POLICY_INSUFFICIENT")
        self._approvals[request.intent.intent_id]=(request.fingerprint,deepcopy(evidence),protection,None)

    def _policy(self, entry_id):
        matches=[event.payload for event in self.ledger.events(entry_id) if event.kind=="live_protection_policy"]
        if not matches:raise ExecutionRefused("DURABLE_EXIT_POLICY_MISSING")
        return matches[0]

    def authorize_exit(self, entry_id, request, evidence, *, owner_confirmation=None):
        reasons=self.readiness(request,evidence,entry=False)
        if reasons:raise ExecutionRefused(",".join(reasons))
        self._owner(owner_confirmation)
        self.transport.verify_scope()
        parent=self.ledger.get(entry_id)
        if parent is None or parent.state not in (OrderState.FILLED,OrderState.CANCELED):
            raise ExecutionRefused("RECONCILE_OR_CANCEL_ENTRY_BEFORE_EXIT")
        pi,i=parent.request.intent,request.intent
        if (i.side is not Side.SELL or i.purpose!="risk_reducing_exit"
                or (i.account_id,i.symbol,i.base_asset,i.quote_asset)!=(pi.account_id,pi.symbol,pi.base_asset,pi.quote_asset)):
            raise ExecutionRefused("EXIT_PARENT_IDENTITY_MISMATCH")
        policy=self._policy(entry_id)
        e=evidence.checks
        if e.bid>Decimal(policy["initial_stop"]) or not Decimal(policy["minimum_sell_price"])<=i.price_quote_per_base<=e.bid:
            raise ExecutionRefused("STOP_NOT_TRIGGERED_OR_EXIT_PRICE_OUTSIDE_POLICY")
        if self.transport.open_orders(i.symbol):
            raise ExecutionRefused("OPEN_ORDERS_SELF_TRADE_OR_EXPOSURE")
        receipt=self.ledger.receipt(entry_id)
        if receipt.known_net_acquired_base is None:
            raise ExecutionRefused("UNKNOWN_ACQUIRED_BASE_OR_FEES")
        rate=Fraction(Decimal(policy["maximum_base_fee_rate"]))
        required=Fraction(i.quantity_base)*(1+rate)
        consumed=Fraction(0)
        for record in self.ledger.records(i.account_id):
            if record.request.intent.side is Side.SELL:
                if record.reservation_held or record.state in _UNRESOLVED:
                    raise ExecutionRefused("EXIT_ALREADY_HELD_OR_UNCERTAIN")
                for f in self.ledger.fills(record.request.intent.intent_id):
                    if f.fee_amount is None:raise ExecutionRefused("UNKNOWN_EXIT_FEES")
                    consumed+=Fraction(f.quantity_base)+(Fraction(f.fee_amount) if f.fee_asset==i.base_asset else 0)
        if required+consumed>Fraction(receipt.known_net_acquired_base) or required>Fraction(evidence.balances.row(i.base_asset)[2]):
            raise ExecutionRefused("EXIT_WOULD_OVERSELL_POSITION_OR_FEES")
        if Fraction(request.reservation.amount)<required:
            raise ExecutionRefused("EXIT_BASE_FEE_RESERVATION_SHORTFALL")
        self._approvals[i.intent_id]=(request.fingerprint,deepcopy(evidence),None,entry_id)

    def _validate_submission(self, request):
        approval=self._approvals.get(request.intent.intent_id)
        if approval is None or approval[0]!=request.fingerprint:
            raise ExecutionRefused("REQUEST_NOT_LOCALLY_AUTHORIZED")
        reasons=self.readiness(request,approval[1],entry=request.intent.side is Side.BUY)
        if reasons:raise ExecutionRefused(",".join(reasons))
        self.transport.bind(request.intent)

    def _check_exposure(self, request, records):
        approval=self._approvals[request.intent.intent_id]
        self.transport.verify_scope()
        current=self.transport.balances()
        if current.assets!=approval[1].balances.assets:
            raise ExecutionRefused("BALANCES_CHANGED_SINCE_AUTHORIZATION")
        if request.intent.side is Side.BUY:
            super()._check_exposure(request,records)
            if self.transport.open_orders(request.intent.symbol):
                raise ExecutionRefused("OPEN_ORDERS_BLOCK_ENTRY")
            policy=approval[2]
            if any(e.kind=="live_protection_policy" for e in self.ledger.events(request.intent.intent_id)):
                raise ExecutionRefused("EXISTING_PROTECTION_WITHOUT_INTENT_REQUIRES_RECOVERY")
            self.ledger.record_event("live_protection_policy",account_id=request.intent.account_id,
                run_id=request.intent.run_id,trace_id=request.intent.trace_id,at=self._now(),intent_id=request.intent.intent_id,
                payload={"initial_stop":policy.initial_stop,"minimum_sell_price":policy.minimum_sell_price,
                    "maximum_base_fee_rate":policy.maximum_base_fee_rate,"before":approval[1].balances})
        else:
            parent=approval[3]
            self.authorize_exit(parent,request,approval[1],owner_confirmation="AUTHORIZE WALLEX LIVE "+self.transport.account_id)
            if any(r.request.intent.intent_id!=parent and (r.reservation_held or r.state in _UNRESOLVED) for r in records):
                raise ExecutionRefused("OTHER_HELD_EXPOSURE")
            self.ledger.record_event("live_exit_parent",account_id=request.intent.account_id,
                run_id=request.intent.run_id,trace_id=request.intent.trace_id,at=self._now(),intent_id=request.intent.intent_id,
                payload={"entry_intent_id":parent})

    def _before_send(self, request):
        self._validate_submission(request)
        self.transport._permit=_WritePermit(request.intent.account_id,request.intent.client_id,"SUBMIT",self._now()+timedelta(seconds=5))

    def cancel(self, intent_id, *, evidence=None, owner_confirmation=None):
        self._owner(owner_confirmation)
        self.transport.verify_scope()
        record=self.ledger.get(intent_id)
        if record is None:raise ExecutionRefused("UNKNOWN_INTENT")
        reasons=self.readiness(record.request,evidence,entry=False,settlement=True)
        if reasons:raise ExecutionRefused(",".join(reasons))
        self.transport._permit=_WritePermit(record.request.intent.account_id,record.request.intent.client_id,"CANCEL",self._now()+timedelta(seconds=5))
        return super().cancel(intent_id)

    def _uncertain(self, record, reason):
        code=self.transport.last_failure_code
        if code:
            self.ledger.record_event("wallex_operation_blocked",account_id=record.request.intent.account_id,
                run_id=record.request.intent.run_id,trace_id=record.request.intent.trace_id,at=self._now(),
                intent_id=record.request.intent.intent_id,payload={"reason":code,"http_status":self.transport.last_http_status})
        return super()._uncertain(record,reason)

    def _accept(self, record, observation):
        result=super()._accept(record,observation)
        self.ledger.record_event("wallex_response_hash",account_id=record.request.intent.account_id,
            run_id=record.request.intent.run_id,trace_id=record.request.intent.trace_id,at=self._now(),
            intent_id=record.request.intent.intent_id,payload={"raw_sha256":self.transport.last_hash,
                "transport":"HTTPS" if self.transport.actual_https else "HTTP_MOCK"})
        return result

    def settle(self, entry_id, exit_id, evidence):
        """Exact fee-aware cashflow -> scoped balance delta; no equity inference.

        Only matching terminal fills, zero remaining base and complete external
        coverage proof can release reservations. Mock settlement is labelled.
        """
        with self.ledger.writer(self.transport.account_id):
            entry,exit=self.ledger.get(entry_id),self.ledger.get(exit_id)
            if entry is None or exit is None or entry.state not in (OrderState.FILLED,OrderState.CANCELED) or exit.state is not OrderState.FILLED:
                raise ExecutionRefused("TERMINAL_ENTRY_AND_EXIT_REQUIRED")
            links=[e.payload.get("entry_intent_id") for e in self.ledger.events(exit_id) if e.kind=="live_exit_parent"]
            if not links or any(parent!=entry_id for parent in links):
                raise ExecutionRefused("EXIT_PARENT_NOT_DURABLE")
            reasons=self.readiness(exit.request,evidence,entry=False,settlement=True)
            if reasons:raise ExecutionRefused(",".join(reasons))
            i=entry.request.intent
            if self.transport.open_orders(i.symbol):raise ExecutionRefused("OPEN_ORDERS_BLOCK_SETTLEMENT")
            self.transport.trades(i.symbol)  # supplemental only; never invent fill attribution.
            after=self.transport.balances()
            before=self._policy(entry_id)["before"]
            before_assets={r[0]:tuple(Decimal(v) for v in r[1:]) for r in before["assets"]}
            deltas={i.base_asset:Fraction(0),i.quote_asset:Fraction(0)}
            for record in (entry,exit):
                buy=record.request.intent.side is Side.BUY
                for f in self.ledger.fills(record.request.intent.intent_id):
                    if f.fee_amount is None or f.fee_asset not in deltas:raise ExecutionRefused("FEES_UNVERIFIED")
                    deltas[i.base_asset]+=Fraction(f.quantity_base)*(1 if buy else -1)
                    deltas[i.quote_asset]+=Fraction(f.quantity_base)*Fraction(f.price_quote_per_base)*(-1 if buy else 1)
                    deltas[f.fee_asset]-=Fraction(f.fee_amount)
            if deltas[i.base_asset]!=0:raise ExecutionRefused("UNSETTLED_BASE_OR_DUST")
            if {r[0] for r in after.assets}!=set(before_assets):raise ExecutionRefused("BALANCE_CURRENCY_SET_CHANGED")
            for asset,total,available,frozen in after.assets:
                expected=Fraction(before_assets[asset][0])+deltas.get(asset,Fraction(0))
                if Fraction(total)!=expected or frozen!=0 or available!=total:
                    raise ExecutionRefused("SETTLEMENT_BALANCE_OR_FEE_MISMATCH")
            report={"status":"MOCK_SETTLED" if not self.transport.actual_https else "SCOPED_BALANCE_RECONCILED",
                "quote_asset":i.quote_asset,"base_delta":"0","quote_delta_fraction":str(deltas[i.quote_asset]),
                "balance_raw_sha256":after.raw_sha256,"action":"NO_TRADE","executable_ready":False}
            self.ledger.release_live_round_trip((entry_id,exit_id),at=self._now(),report=report)
            return report
