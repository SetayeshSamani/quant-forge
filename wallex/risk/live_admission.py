"""Explicit execution admission atop unchanged diagnostic TP-1/Risk logic.

A reviewed authority is a trusted backend input, NOT something GET success or
an .env flag can manufacture. Missing provider/policy/equity evidence blocks.
This pure check performs no writes and never selects a risk profile or equity.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import re
from hashlib import sha256
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .pipeline import assess_risk_pipeline
from .sizing import RiskProfile
from wallex.execution.models import amount, utc, canonical_json
from wallex.execution.preflight import VenueSnapshot


@dataclass(frozen=True)
class ReviewedLiveAuthority:
    account_id: str = field(repr=False)
    symbol: str
    profile: RiskProfile
    valid_until: datetime
    maximum_capital_quote: Decimal
    maximum_order_notional: Decimal
    maximum_entry_slippage_fraction: Decimal
    fee_asset: str
    # Exact reviewed artifacts; these are NOT inferred by the readonly collector.
    permission_sha256: str
    credit_eligibility_sha256: str
    equity_state_sha256: str
    persistent_loss_state_sha256: str
    fee_asset_policy_sha256: str
    exit_recovery_policy_sha256: str
    origin: str  # PROVIDER_EVIDENCE_REVIEWED or HTTP_MOCK; never interchangeable.


@dataclass(frozen=True)
class LiveAdmissionInputs:
    venue: VenueSnapshot
    authority: ReviewedLiveAuthority
    ai_record: dict | None = field(default=None,repr=False)
    _audit_verified: bool = field(default=False,init=False,repr=False)

    @classmethod
    def from_audit(cls,venue,authority,*,ledger_path,event_seq,evidence_hash):
        """Read back an actual provider result from the existing observation ledger.

        Never accept a frontend approval flag or a synthetic transport result.
        """
        from wallex.infra.agent_runtime import PARTITION
        if type(event_seq) is not int or event_seq<1:raise ValueError('AI_AUDIT_EVENT_REQUIRED')
        with closing(sqlite3.connect(Path(ledger_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute("SELECT payload_json FROM audit_events WHERE event_seq=? AND kind=? AND account_id=?",
                (event_seq,'public_tp1_ai_review',PARTITION)).fetchone()
        if row is None or len(row[0])>1048576:raise ValueError('AI_AUDIT_EVENT_UNAVAILABLE')
        report=json.loads(row[0]);records=report.get('records')
        if report.get('inference_succeeded') is not True or type(records) is not list or not 1<=len(records)<=5:
            raise ValueError('REAL_AI_AUDIT_UNAVAILABLE')
        matches=[r for r in records if type(r) is dict and r.get('evidence_hash')==evidence_hash
            and r.get('inference_source')=='BOXAPI_HTTPS' and r.get('evidence_kind')=='historical_data'
            and r.get('status')=='OK' and r.get('http_status')==200]
        if len(matches)!=1:raise ValueError('GENUINE_AI_EVIDENCE_NOT_UNIQUE')
        result=cls(venue,authority,matches[0]);object.__setattr__(result,'_audit_verified',True)
        return result


@dataclass(frozen=True)
class LiveAdmission:
    risk_checks_passed: bool
    reasons: tuple[str,...]
    request_fingerprint: str
    # Even a pass still requires durable gateway admission and exact owner ticket.
    action: str=field(default='NO_TRADE',init=False)
    executable_ready: bool=field(default=False,init=False)


def assess_live_admission(request,risk_inputs,context,*,now,actual_https):
    reasons=[]
    fingerprint=getattr(request,"fingerprint","")
    try:
        now=utc(now)
        if type(context) is not LiveAdmissionInputs:
            return LiveAdmission(False,('EXPLICIT_LIVE_RISK_CONTEXT_REQUIRED',),fingerprint)
        v,a=context.venue,context.authority
        if type(v) is not VenueSnapshot or type(a) is not ReviewedLiveAuthority:raise ValueError()
        i=request.intent
        for value in (v.price_step,v.quantity_step,v.minimum_quantity,v.minimum_notional):amount(value)
        for value in (v.maker_rate,v.taker_rate):amount(value,zero=True)
        if max(v.maker_rate,v.taker_rate)>=1:raise ValueError()
        if any(type(h) is not str or re.fullmatch('[a-f0-9]{64}',h) is None for _,h in v.source_hashes):raise ValueError()
        if v.book.get('raw_sha256')!=dict(v.source_hashes).get('depth'):raise ValueError()
        expected='HTTPS' if actual_https else 'HTTP_MOCK'
        if actual_https and context._audit_verified is not True:reasons.append('REAL_AI_AUDIT_READBACK_REQUIRED')
        if v.origin!=expected or a.origin!=('PROVIDER_EVIDENCE_REVIEWED' if actual_https else 'HTTP_MOCK'):
            reasons.append('SYNTHETIC_OR_UNREVIEWED_LIVE_AUTHORITY')
        if (i.account_id,i.symbol,i.base_asset,i.quote_asset)!=(v.account_id,v.symbol,v.base_asset,v.quote_asset) or (a.account_id,a.symbol)!=(i.account_id,i.symbol):
            reasons.append('LIVE_ADMISSION_ACCOUNT_OR_MARKET_MISMATCH')
        if not 0<=(now-utc(v.observed_at)).total_seconds()<=5 or not 0<(utc(a.valid_until)-now).total_seconds()<=86400:
            reasons.append('LIVE_ACCOUNT_OR_AUTHORITY_EXPIRED')
        for name in ('permission_sha256','credit_eligibility_sha256','equity_state_sha256','persistent_loss_state_sha256','fee_asset_policy_sha256','exit_recovery_policy_sha256'):
            if type(getattr(a,name)) is not str or re.fullmatch('[a-f0-9]{64}',getattr(a,name)) is None:
                reasons.append('REVIEWED_PERMISSION_CREDIT_EQUITY_OR_EXIT_EVIDENCE_MISSING')
        book=v.book;event=book.get('source_event_time');receipt=book.get('received_at')
        if (type(event) is not int or type(receipt) is not int or book.get('verified_freshness')!='VERIFIED_PROVIDER_EVENT_TIME'
                or not 0<=int(now.timestamp())-event<=5 or not event<=receipt<=int(now.timestamp())):
            reasons.append('EXECUTABLE_QUOTE_PROVIDER_FRESHNESS_UNVERIFIED')
        bid,ask=book.get('best_bid'),book.get('best_ask')
        for value in (bid,ask,a.maximum_capital_quote,a.maximum_order_notional):amount(value)
        amount(a.maximum_entry_slippage_fraction,zero=True)
        if bid>=ask or a.maximum_entry_slippage_fraction>=1:raise ValueError()
        if v.open_orders or any(json.loads(r).get('active') is True for r in v.orders):
            reasons.append('OPEN_ORDERS_PREVENT_ATOMIC_ADMISSION')
        if v.balances.row(i.base_asset)[1]!=0 or any(row[1]>0 for row in v.balances.assets if row[0] not in (i.base_asset,i.quote_asset)):
            reasons.append('ACCOUNT_NOT_FLAT_OR_RECONCILED')
        if any(row[3]>0 for row in v.balances.assets):reasons.append('FROZEN_FUNDS_REQUIRE_RECONCILIATION')
        assessed=assess_risk_pipeline(risk_inputs)
        r=risk_inputs
        if not assessed.local_conditions_satisfied or assessed.sizing is None or not assessed.sizing.calculation_valid:
            reasons.append('TP1_LOSS_PORTFOLIO_OR_SIZING_REJECTED')
        else:
            if Fraction(i.quantity_base)>Fraction(assessed.sizing.theoretical_quantity_upper_bound):reasons.append('LIVE_RISK_QUANTITY_EXCEEDED')
        ai=context.ai_record
        if assessed.frame is None or type(ai) is not dict:
            reasons.append('AUDITED_CURRENT_AI_RETAIN_DECISION_REQUIRED')
        else:
            material={'metadata':assessed.frame.metadata,'trend':assessed.frame.trend,'pullback':assessed.frame.pullback,
                'sources':ai.get('source_hashes'),'source_commit':ai.get('source_commit'),'evidence_kind':ai.get('evidence_kind')}
            digest=sha256(canonical_json(material).encode('ascii')).hexdigest()
            if (ai.get('status')!='OK' or ai.get('http_status')!=200 or ai.get('advisory_decision')!='REVIEW'
                    or ai.get('retained_for_research') is not True or ai.get('technical_eligible') is not True
                    or ai.get('inference_source')!=('BOXAPI_HTTPS' if actual_https else 'SYNTHETIC_TEST_TRANSPORT')
                    or ai.get('evidence_kind')!=('historical_data' if actual_https else 'synthetic_fixture')
                    or ai.get('evidence_hash')!=digest or ai.get('as_of')!=r.frame.as_of
                    or type(ai.get('completed_at')) is not int or not 0<=int(now.timestamp())-ai['completed_at']<=30
                    or type(ai.get('response_sha256')) is not str or re.fullmatch('[a-f0-9]{64}',ai['response_sha256']) is None):
                reasons.append('AI_VETO_MISSING_OR_EVIDENCE_MISMATCH')
        if (r.account_id!=i.account_id or r.quote_asset!=i.quote_asset or r.profile is not a.profile
                or r.frame.symbol!=i.symbol or not 0<=int(now.timestamp())-r.frame.as_of<=5):
            reasons.append('LIVE_RISK_IDENTITY_PROFILE_OR_CUTOFF_MISMATCH')
        # Source equity and historical latches must be explicitly reviewed above.
        # Wallet totals are only a funding constraint, NEVER equity or high-water.
        if r.loss_state is None or not 0<=int(now.timestamp())-r.loss_state.equity_observed_at<=5:
            reasons.append('LIVE_EQUITY_EVIDENCE_STALE')
        price,quantity=Fraction(i.price_quote_per_base),Fraction(i.quantity_base)
        if price>Fraction(r.pe) or Fraction(ask)>Fraction(r.pe) or price>Fraction(ask)*(1+Fraction(a.maximum_entry_slippage_fraction)):
            reasons.append('EXECUTABLE_PE_OR_SLIPPAGE_EXCEEDED')
        if ((price/Fraction(v.price_step)).denominator!=1 or (quantity/Fraction(v.quantity_step)).denominator!=1
                or quantity<Fraction(v.minimum_quantity) or price*quantity<Fraction(v.minimum_notional)):
            reasons.append('LIVE_INSTRUMENT_INCREMENT_OR_MINIMUM_VIOLATION')
        if v.maximum_quantity is None:reasons.append('LIVE_VENUE_MAXIMUM_QUANTITY_UNVERIFIED')
        elif quantity>Fraction(v.maximum_quantity):reasons.append('LIVE_VENUE_MAXIMUM_QUANTITY_EXCEEDED')
        if v.maximum_quantity is not None:
            from .gate import EntryRequest,RiskLimits,evaluate_entry
            gate=evaluate_entry(EntryRequest(i.quantity_base,i.price_quote_per_base),RiskLimits(v.maximum_quantity,a.maximum_order_notional))
            if not gate.allowed:reasons.append('LIVE_RISK_GATE_REJECTED')
        rate=max(v.maker_rate,v.taker_rate)
        funded=price*quantity*(1+Fraction(rate))
        if (a.fee_asset not in (i.base_asset,i.quote_asset) or funded>Fraction(r.free_quote)
                or funded>Fraction(v.balances.row(i.quote_asset)[2]) or funded>Fraction(a.maximum_capital_quote)
                or price*quantity>Fraction(a.maximum_order_notional)):
            reasons.append('LIVE_FEE_FUNDING_OR_CAPITAL_LIMIT')
    except Exception:
        reasons.append('INVALID_LIVE_ADMISSION_INPUTS')
    return LiveAdmission(not reasons,tuple(dict.fromkeys(reasons)) or ('VERIFIED_LOCAL_LIVE_RISK_CHECKS_PASSED',),fingerprint)


def persist_live_loss_state(ledger,state,*,account_id,equity_source_sha256,origin):
    """Persist explicit independently valued equity and sticky breach latches.

    No wallet conversion or implicit day/profile reset. Day-start equity is
    supplied explicitly; recovery cannot lower the recorded high-water or clear
    either prior breach. A reviewed source hash is mandatory, not a price guess.
    """
    from dataclasses import replace
    from .loss_guards import assess_loss_guards
    from wallex.execution.models import Mode
    if ledger.mode is not Mode.LIVE or origin not in ('PROVIDER_EVIDENCE_REVIEWED','HTTP_MOCK'):
        raise ValueError('EXPLICIT_LIVE_LOSS_PROVENANCE_REQUIRED')
    if type(equity_source_sha256) is not str or re.fullmatch('[a-f0-9]{64}',equity_source_sha256) is None:
        raise ValueError('EQUITY_SOURCE_EVIDENCE_REQUIRED')
    with ledger.writer(account_id):
        previous=[e.payload for e in ledger.events() if e.account_id==account_id and e.kind=='live_loss_guard_state']
        if previous:
            old=previous[-1]['state']
            if (state.quote_asset!=old['quote_asset'] or state.policy.profile.value!=old['policy']['profile']
                    or state.as_of<old['as_of']):raise ValueError('LIVE_LOSS_STATE_IDENTITY_OR_TIME_CHANGED')
            if state.day_start_at==old['day_start_at'] and state.day_start_equity!=Decimal(old['day_start_equity']):
                raise ValueError('DAY_START_EQUITY_CANNOT_BE_REWRITTEN')
            state=replace(state,high_water_equity=max(state.high_water_equity,Decimal(old['high_water_equity']),state.current_equity),
                prior_daily_breach=state.prior_daily_breach or old['prior_daily_breach'],
                prior_drawdown_breach=state.prior_drawdown_breach or old['prior_drawdown_breach'])
        assessed=assess_loss_guards(state)
        if not assessed.calculation_valid:raise ValueError('INVALID_VERIFIED_LOSS_STATE')
        state=replace(state,prior_daily_breach=assessed.daily_breach_latched,prior_drawdown_breach=assessed.drawdown_breach_latched)
        payload={'state':json.loads(canonical_json(state)),'equity_source_sha256':equity_source_sha256,'origin':origin}
        digest=sha256(canonical_json(payload).encode('ascii')).hexdigest()
        ledger.record_event('live_loss_guard_state',account_id=account_id,run_id='verified-equity',trace_id='loss-latches',
            at=datetime.fromtimestamp(state.as_of,__import__('datetime').timezone.utc),payload=payload)
        return state,digest


def verify_live_loss_history(ledger,risk_inputs,authority):
    """Read-only exact binding to durable account state; no default baseline."""
    try:
        from wallex.execution.models import json_value
        history=[e.payload for e in ledger.events() if e.account_id==risk_inputs.account_id and e.kind=='live_loss_guard_state']
        if not history:return False
        stored=history[-1]
        return (stored['origin']=='PROVIDER_EVIDENCE_REVIEWED'
            and stored['state']==json_value(risk_inputs.loss_state)
            and stored['equity_source_sha256']==authority.equity_state_sha256
            and sha256(canonical_json(stored).encode('ascii')).hexdigest()==authority.persistent_loss_state_sha256)
    except Exception:return False
