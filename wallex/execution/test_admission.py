"""Replay-only integration, explicit synthetic account/market/risk evidence."""

from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wallex.audit.ledger import Ledger
from wallex.risk.test_pipeline import fixture
from .admission import ReplayAdmission,assess_replay_admission,assess_protective_exit
from .fixtures import synthetic_request,synthetic_observation,synthetic_fill
from .gateway import ReplayGateway,ExecutionRefused
from .models import Mode,OrderState,Reservation
from .replay import ReplayTransport,ReplayScenario

D=Decimal


def fixture_request():
    risk=fixture();now=datetime.fromtimestamp(risk.frame.as_of,timezone.utc)
    request=synthetic_request(account_id=risk.account_id)
    intent=replace(request.intent,symbol=risk.frame.symbol,created_at=now,expires_at=now+timedelta(seconds=20),
                   quantity_base=D('.5'),price_quote_per_base=D(110))
    request=replace(request,intent=intent,reservation=Reservation('QUOTE',D('55.1'),D(100)))
    evidence=ReplayAdmission(risk,True,True,True,intent.symbol,'BASE','QUOTE',D(108),D(109),now,10,True,
                            D(1),D('.01'),D(1),D(1000),D('.001'),(),True)
    return request,evidence,now


class AdmissionTests(unittest.TestCase):
    def test_checked_gateway_reuses_durable_ids_recovery_fills_and_stop_monitor(self):
        request,evidence,now=fixture_request()
        key=(request.intent.account_id,request.intent.client_id)
        partial=synthetic_observation(request,state=OrderState.PARTIAL,cumulative=D('.25'),observed_at=now,
            fills=(synthetic_fill(request,quantity=D('.25'),fee=D('.001'),source_time=now,received_at=now),))
        source=ReplayTransport({key:ReplayScenario(partial,True)})
        with TemporaryDirectory() as directory:
            ledger=Ledger(Path(directory)/'audit.sqlite')
            gateway=ReplayGateway(ledger,source,clock=lambda:now)
            with localcontext() as ctx:
                ctx.prec=3
                self.assertTrue(assess_replay_admission(request,evidence,now=now).local_checks_passed)
            self.assertEqual(gateway.submit_checked(request,evidence).receipt.state,OrderState.UNKNOWN)
            restarted=ReplayGateway(Ledger(ledger.path),source,clock=lambda:now)
            self.assertEqual(restarted.recover_account(key[0],run_id='test',trace_id='test')[0].state,OrderState.PARTIAL)
            self.assertEqual(restarted.submit_checked(request,evidence).disposition,'existing_no_resubmission')
            self.assertEqual(source.submit_calls,[key])
            stop=assess_protective_exit(ledger,request.intent.intent_id,stop=D(100),bid=D(99),observed_at=now,now=now,max_age_seconds=5)
            self.assertTrue(stop['stop_triggered'])
            self.assertEqual(stop['confirmed_net_base_fraction'],'249/1000')
            self.assertIn('EXIT_TRANSPORT_AND_SETTLEMENT_UNIMPLEMENTED',stop['reasons'])
            self.assertIn('ENTRY_RECONCILIATION_OR_CANCELLATION_REQUIRED',stop['reasons'])
            self.assertFalse(stop['executable_ready'])

    def test_identity_limits_fees_risk_scope_freshness_self_trade_fail_closed(self):
        request,evidence,now=fixture_request()
        cases=[replace(evidence,account_scope_verified=False),replace(evidence,trade_permission_verified=False),
            replace(evidence,order_coverage_complete=False),replace(evidence,market_symbol='WRONG'),
            replace(evidence,price_step=D(3)),replace(evidence,quantity_step=D('.3')),
            replace(evidence,min_notional=D(100)),replace(evidence,fee_quote_rate=D('.1')),
            replace(evidence,ask=D(111)),replace(evidence,observed_at=now-timedelta(seconds=11)),
            replace(evidence,observed_at=now+timedelta(seconds=1)),replace(evidence,bid=D('NaN')),
            replace(evidence,opposing_sell_prices=(D(110),)),replace(evidence,risk_inputs=None),
            replace(evidence,risk_inputs=replace(evidence.risk_inputs,equity=D(0))),
            replace(evidence,risk_inputs=replace(evidence.risk_inputs,account_id='wrong'))]
        with TemporaryDirectory() as directory:
            source=ReplayTransport({});gateway=ReplayGateway(Ledger(Path(directory)/'a.sqlite'),source,clock=lambda:now)
            for index,case in enumerate(cases):
                with self.subTest(case=index):
                    self.assertFalse(assess_replay_admission(request,case,now=now).local_checks_passed)
                    with self.assertRaises(ExecutionRefused):gateway.submit_checked(request,case)
            self.assertFalse(assess_replay_admission(replace(request,intent=replace(request.intent,mode=Mode.LIVE)),evidence,now=now).local_checks_passed)
            self.assertEqual(source.submit_calls,[])
