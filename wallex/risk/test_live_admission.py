"""Explicit synthetic live-admission scenarios, never real authorization."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal
from hashlib import sha256
import unittest

from .pipeline import assess_risk_pipeline
from .live_admission import ReviewedLiveAuthority,LiveAdmissionInputs,assess_live_admission,persist_live_loss_state
from wallex.execution.models import canonical_json
from wallex.execution.test_preflight import ReadTape
from wallex.execution.test_wallex_live import setup_inputs,KEY,ACCOUNT
from wallex.execution.wallex_http import WallexExchange
from wallex.execution.preflight import collect_venue


class LiveAdmissionTests(unittest.TestCase):
    def inputs(self):
        request,checks,_,now=setup_inputs()
        tape=ReadTape(now);exchange=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
        venue=collect_venue(exchange,symbol='BTCTMN',base_asset='BTC',quote_asset='TMN')
        # Only this explicitly SYNTHETIC fixture supplies missing provider facts.
        venue=replace(venue,maximum_quantity=Decimal(10),book={**venue.book,
            'source_event_time':int(now.timestamp()),'verified_freshness':'VERIFIED_PROVIDER_EVENT_TIME'})
        authority=ReviewedLiveAuthority(ACCOUNT,'BTCTMN',checks.risk_inputs.profile,now+timedelta(minutes=5),
            Decimal(1000),Decimal(100),Decimal('.02'),'TMN',*('a'*64 for _ in range(6)),'HTTP_MOCK')
        assessed=assess_risk_pipeline(checks.risk_inputs)
        ai={'source_hashes':{'h1':'a'*64,'m1':'b'*64},'source_commit':'c'*40,'evidence_kind':'synthetic_fixture',
            'as_of':checks.risk_inputs.frame.as_of,'completed_at':int(now.timestamp()),'status':'OK','http_status':200,
            'advisory_decision':'REVIEW','retained_for_research':True,'technical_eligible':True,
            'inference_source':'SYNTHETIC_TEST_TRANSPORT','response_sha256':'d'*64}
        material={'metadata':assessed.frame.metadata,'trend':assessed.frame.trend,'pullback':assessed.frame.pullback,
            'sources':ai['source_hashes'],'source_commit':ai['source_commit'],'evidence_kind':ai['evidence_kind']}
        ai['evidence_hash']=sha256(canonical_json(material).encode('ascii')).hexdigest()
        return request,checks.risk_inputs,LiveAdmissionInputs(venue,authority,ai),now
    def test_explicit_admission_reuses_risk_and_binds_genuine_candidate_evidence(self):
        request,risk,context,now=self.inputs()
        outcome=assess_live_admission(request,risk,context,now=now,actual_https=False)
        self.assertTrue(outcome.risk_checks_passed,outcome.reasons)
        self.assertFalse(outcome.executable_ready)
        self.assertFalse(assess_risk_pipeline(risk).executable_ready) # old public contract remains unchanged.
        self.assertEqual(outcome.request_fingerprint,request.fingerprint)
        self.assertFalse(assess_live_admission(request,risk,context,now=now,actual_https=True).risk_checks_passed)
    def test_missing_stale_or_mismatched_authority_quote_risk_and_ai_fail_closed(self):
        request,risk,c,now=self.inputs()
        cases=[None,replace(c,authority=replace(c.authority,permission_sha256='')),
            replace(c,authority=replace(c.authority,maximum_capital_quote=Decimal(1))),
            replace(c,venue=replace(c.venue,maximum_quantity=None)),
            replace(c,venue=replace(c.venue,observed_at=now-timedelta(seconds=6))),
            replace(c,venue=replace(c.venue,book={**c.venue.book,'source_event_time':None})),
            replace(c,ai_record={**c.ai_record,'advisory_decision':'NO_TRADE'}),
            replace(c,ai_record={**c.ai_record,'evidence_hash':'f'*64})]
        for index,bad in enumerate(cases):
            with self.subTest(index=index):self.assertFalse(assess_live_admission(request,risk,bad,now=now,actual_https=False).risk_checks_passed)
        self.assertFalse(assess_live_admission(request,replace(risk,equity=None),c,now=now,actual_https=False).risk_checks_passed)

        from wallex.audit.ledger import Ledger
        from wallex.execution.models import Mode
        with TemporaryDirectory() as directory:
            ledger=Ledger(Path(directory)/'live-risk.sqlite',mode=Mode.LIVE)
            breached=replace(risk.loss_state,current_equity=Decimal(9900))
            persisted,_=persist_live_loss_state(ledger,breached,account_id=ACCOUNT,equity_source_sha256='e'*64,origin='HTTP_MOCK')
            self.assertTrue(persisted.prior_daily_breach)
            restarted=Ledger(ledger.path,mode=Mode.LIVE)
            recovered,_=persist_live_loss_state(restarted,risk.loss_state,account_id=ACCOUNT,equity_source_sha256='f'*64,origin='HTTP_MOCK')
            self.assertTrue(recovered.prior_daily_breach) # recovery cannot clear a breach.
            tomorrow=replace(recovered,as_of=recovered.as_of+86400,equity_observed_at=recovered.equity_observed_at+86400,
                day_start_at=recovered.day_start_at+86400,day_start_equity=Decimal(10000),prior_daily_breach=False)
            rolled,_=persist_live_loss_state(restarted,tomorrow,account_id=ACCOUNT,equity_source_sha256='f'*64,origin='HTTP_MOCK')
            self.assertTrue(rolled.prior_daily_breach) # explicit new day does not clear it either.
