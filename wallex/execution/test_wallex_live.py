"""Actual Wallex REST adapter + durable gateway, injected HTTP only; never network."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from wallex.audit.ledger import Ledger,LedgerError
from wallex.risk.test_pipeline import fixture as risk_fixture
from .fixtures import synthetic_request
from .models import Mode,OrderState,Reservation,Side,decimal_text
from .admission import ReplayAdmission
from .live import WallexGateway,LiveEvidence,ProtectionPolicy,REQUIRED_PROOFS
from .gateway import ExecutionRefused
from .wallex_http import WallexExchange,WireResponse,HTTPSExchangeTransport,ExchangeUnavailable,WireRequest

from .synthetic import D,KEY,ACCOUNT,owner,setup_inputs,HttpTape


class WallexLiveTests(unittest.TestCase):
    def make(self,directory):
        request,checks,policy,now=setup_inputs();tape=HttpTape(now)
        exchange=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
        ledger=Ledger(Path(directory)/'live.sqlite',mode=Mode.LIVE)
        gateway=WallexGateway(ledger,exchange,clock=lambda:now)
        return request,checks,policy,now,tape,exchange,ledger,gateway

    def evidence(self,exchange,checks):
        return LiveEvidence(checks,exchange.balances(),tuple((k,'a'*64) for k in sorted(REQUIRED_PROOFS)),'HTTP_MOCK',D('.01'),D(10),'TMN')

    def close_position(self,request,checks,now,tape,exchange,ledger,gateway):
        parent=ledger.receipt(request.intent.intent_id)
        quantity=parent.known_net_acquired_base
        sell=replace(request,intent=replace(request.intent,intent_id='live_sell',client_id='SELL_1',side=Side.SELL,
            purpose='risk_reducing_exit',quantity_base=quantity,price_quote_per_base=D(97)),
            reservation=Reservation('BTC',quantity,quantity))
        e=self.evidence(exchange,replace(checks,bid=D(97),ask=D(98)))
        self.assertEqual(gateway.inspect_protection(request.intent.intent_id,e)['status'],'PROTECTIVE_SELL_REQUIRES_EXACT_APPROVAL')
        gap=replace(e,checks=replace(e.checks,bid=D(90),ask=D(91)))
        self.assertEqual(gateway.inspect_protection(request.intent.intent_id,gap)['status'],'GAP_BELOW_APPROVED_FLOOR_ESCALATE_NO_GUARANTEED_STOP')
        self.assertTrue(gateway.stop_new_entries)
        gateway.authorize_exit(request.intent.intent_id,sell,e,owner_confirmation=owner(sell))
        self.assertEqual(gateway.submit(sell).receipt.state,OrderState.WORKING)
        tape.fill('SELL_1',quantity)
        self.assertEqual(gateway.reconcile('live_sell').state,OrderState.FILLED)
        report=gateway.settle('live_buy','live_sell',self.evidence(exchange,replace(checks,bid=D(97),ask=D(98))))
        self.assertEqual(report['status'],'MOCK_SETTLED')
        self.assertFalse(report['executable_ready'])
        self.assertEqual(tape.base,D(0))
        self.assertFalse(ledger.get('live_buy').reservation_held)
        self.assertFalse(ledger.get('live_sell').reservation_held)
        return report

    def test_http_buy_partial_fill_full_fill_protective_sell_and_tmn_settlement(self):
        with TemporaryDirectory() as directory:
            request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
            e=self.evidence(exchange,checks)
            self.assertEqual(gateway.readiness(request,e),())
            with self.assertRaises(ExecutionRefused):gateway.submit(request)
            gateway.authorize_entry(request,e,policy,owner_confirmation=owner(request))
            self.assertEqual(gateway.submit(request).receipt.state,OrderState.WORKING)
            tape.fill('BUY_1',D('.25'))
            self.assertEqual(gateway.reconcile('live_buy').state,OrderState.PARTIAL)
            tape.fill('BUY_1',D('.25'))
            self.assertEqual(gateway.reconcile('live_buy').state,OrderState.FILLED)
            self.assertEqual(len(ledger.fills('live_buy')),2) # identical economic rows remain two distinct occurrences.
            report=self.close_position(request,checks,now,tape,exchange,ledger,gateway)
            self.assertEqual(report['quote_delta_fraction'],str(__import__('fractions').Fraction(D('-6.6035'))))
            self.assertEqual(tape.quote,D('9993.3965'))
            self.assertEqual(len([r for r in tape.requests if r.method=='POST']),2)
            self.assertTrue(any(r.method=='GET' and '/trades?' in r.path for r in tape.requests))
            private=[r for r in tape.requests if r.method=='POST'][0]
            self.assertNotIn(KEY,repr(private))
            self.assertNotIn(KEY,' '.join(e.payload_json for e in ledger.events()))

    def test_lost_response_and_crash_recovery_never_resubmit_partial_entry(self):
        for fault in ('timeout','crash'):
            with self.subTest(fault=fault),TemporaryDirectory() as directory:
                request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
                e=self.evidence(exchange,checks);gateway.authorize_entry(request,e,policy,owner_confirmation=owner(request))
                tape.fault=fault
                if fault=='crash':
                    with self.assertRaises(KeyboardInterrupt):gateway.submit(request)
                    self.assertEqual(ledger.get('live_buy').state,OrderState.SUBMITTING)
                else:self.assertEqual(gateway.submit(request).receipt.state,OrderState.UNKNOWN)
                new_exchange=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
                restarted=WallexGateway(Ledger(ledger.path,mode=Mode.LIVE),new_exchange,clock=lambda:now)
                recovered=restarted.recover_account(ACCOUNT,run_id='recover',trace_id='recover')
                self.assertEqual(recovered[0].state,OrderState.PARTIAL)
                self.assertEqual(restarted.submit(request).disposition,'existing_no_resubmission')
                self.assertEqual(len([r for r in tape.requests if r.method=='POST']),1)
                self.assertEqual(restarted.cancel('live_buy',evidence=self.evidence(new_exchange,checks),owner_confirmation=owner(request,'CANCEL')).state,OrderState.CANCELED)
                self.close_position(request,checks,now,tape,new_exchange,ledger,restarted)
                self.assertEqual(tape.quote,D('9996.69825'))

        # Crash after CANCELED is committed but before residual hold release.
        with TemporaryDirectory() as directory:
            request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
            gateway.authorize_entry(request,self.evidence(exchange,checks),policy,owner_confirmation=owner(request))
            gateway.submit(request);tape.fill('BUY_1',D('.5'));gateway.reconcile('live_buy')
            sell=replace(request,intent=replace(request.intent,intent_id='live_sell',client_id='SELL_1',side=Side.SELL,
                purpose='risk_reducing_exit',price_quote_per_base=D(97)),reservation=Reservation('BTC',D('.5'),D('.5')))
            exit_checks=replace(checks,bid=D(97),ask=D(98));e=self.evidence(exchange,exit_checks)
            gateway.authorize_exit('live_buy',sell,e,owner_confirmation=owner(sell));gateway.submit(sell)
            tape.fill('SELL_1',D('.25'));gateway.reconcile('live_sell')
            with patch.object(ledger,'release_canceled_exit',side_effect=KeyboardInterrupt()):
                with self.assertRaises(KeyboardInterrupt):gateway.cancel('live_sell',evidence=self.evidence(exchange,exit_checks),owner_confirmation=owner(sell,'CANCEL'))
            self.assertTrue(ledger.get('live_sell').reservation_held)
            fresh=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
            restarted=WallexGateway(Ledger(ledger.path,mode=Mode.LIVE),fresh,clock=lambda:now)
            restarted.recover_account(ACCOUNT,run_id='cancel-restart',trace_id='cancel-restart')
            self.assertFalse(ledger.get('live_sell').reservation_held)
            self.assertTrue(ledger.get('live_buy').reservation_held)
            self.assertEqual(len([r for r in tape.requests if r.method=='DELETE']),1)

    def test_missing_proofs_owner_scope_risk_limits_and_real_transport_reject_mock(self):
        with TemporaryDirectory() as directory:
            request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
            e=self.evidence(exchange,checks)
            cases=[replace(e,proofs=()),replace(e,origin='LIVE_VERIFIED'),replace(e,checks=replace(checks,trade_permission_verified=False)),
                replace(e,checks=replace(checks,order_coverage_complete=False)),replace(e,checks=replace(checks,fee_quote_rate=D('.1'))),
                replace(e,checks=replace(checks,price_step=D(3))),replace(e,checks=replace(checks,opposing_sell_prices=(D(109),))),
                replace(e,checks=replace(checks,risk_inputs=None)),replace(e,min_quantity=D(1)),replace(e,max_quantity=D('.1')),replace(e,fee_asset='IRR'),replace(e,balances=replace(e.balances,account_id='wrong')),
                replace(e,checks=replace(checks,observed_at=now-timedelta(seconds=31)))]
            for index,bad in enumerate(cases):
                with self.subTest(index=index),self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,bad,policy,owner_confirmation=owner(request))
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,policy)
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,replace(policy,initial_stop=D(100)),owner_confirmation=owner(request))
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,replace(policy,minimum_sell_price=D(90)),owner_confirmation=owner(request))
            with self.assertRaises(ExchangeUnavailable):exchange.submit(request.intent)
            real=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,clock=lambda:now)
            real_gate=WallexGateway(ledger,real,clock=lambda:now)
            self.assertIn('MOCK_OR_UNVERIFIED_EVIDENCE_ON_REAL_TRANSPORT',real_gate.readiness(request,e))
            self.assertIn('EXPLICIT_LIVE_RISK_CONTEXT_REQUIRED',real_gate.readiness(request,replace(e,origin='LIVE_VERIFIED')))
            self.assertFalse(any(r.method in ('POST','DELETE') for r in tape.requests))
            with self.assertRaises(LedgerError):Ledger(ledger.path) # no silent live/replay migration.

    def test_http_errors_malformed_fills_cancel_uncertainty_and_balance_mismatch_hold(self):
        for status in (401,403,404,429,500,422):
            with self.subTest(status=status),TemporaryDirectory() as directory:
                request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
                gateway.authorize_entry(request,self.evidence(exchange,checks),policy,owner_confirmation=owner(request))
                # Fail only POST; admission GET remains working.
                original=tape.send
                def send(wire):
                    if wire.method=='POST':return WireResponse(status,b'{"success":false,"result":{"error_code":1006}}')
                    return original(wire)
                tape.send=send
                self.assertEqual(gateway.submit(request).receipt.state,OrderState.REJECTED if status==422 else OrderState.UNKNOWN)
        for case in ('fee','quantity','identity','cancel','balance'):
            with self.subTest(case=case),TemporaryDirectory() as directory:
                request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
                gateway.authorize_entry(request,self.evidence(exchange,checks),policy,owner_confirmation=owner(request))
                gateway.submit(request);tape.fill('BUY_1',D('.25'))
                if case=='cancel':
                    gateway.reconcile('live_buy');tape.cancel_fault=True
                    self.assertEqual(gateway.cancel('live_buy',evidence=self.evidence(exchange,checks),owner_confirmation=owner(request,'CANCEL')).state,OrderState.UNKNOWN)
                elif case=='balance':
                    gateway.reconcile('live_buy');gateway.cancel('live_buy',evidence=self.evidence(exchange,checks),owner_confirmation=owner(request,'CANCEL'))
                    tape.quote+=D(1)
                    with self.assertRaises(ExecutionRefused):self.close_position(request,checks,now,tape,exchange,ledger,gateway)
                else:
                    row=tape.orders['BUY_1']
                    if case=='fee':row['fills'][0]['feeAsset']='UNKNOWN'
                    if case=='quantity':row['executedQty']='.4'
                    if case=='identity':row['symbol']='WRONG'
                    self.assertEqual(gateway.reconcile('live_buy').state,OrderState.UNKNOWN)
                self.assertTrue(ledger.get('live_buy').reservation_held)

    def test_https_wire_fixed_origin_headers_methods_and_never_follows_redirects(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from .wallex_http import ORIGIN,_NoRedirect
        class Reply(BytesIO):
            status=200
        class Opener:
            def __init__(self):self.requests=[]
            def open(self,wire,timeout):
                self.requests.append((wire,timeout));return Reply(b'{"success":true,"result":{}}')
        opener=Opener()
        for method,path,body in (('POST','/v1/account/orders',b'{"client_id":"BUY_1"}'),('GET','/v1/account/orders/BUY_1',None),
                ('DELETE','/v1/account/orders/BUY_1',None)):
            with self.subTest(method=method),patch('wallex.execution.wallex_http.build_opener',return_value=opener):
                from .live import _WritePermit
                permit=None if method=='GET' else _WritePermit(ACCOUNT,'BUY_1','SUBMIT' if method=='POST' else 'CANCEL',datetime.now(timezone.utc)+timedelta(seconds=5))
                wire=WireRequest(method,path,{'x-api-key':KEY,'sub-account-client-id':ACCOUNT,'Content-Type':'application/json','Accept-Encoding':'identity'},body,permit=permit)
                result=HTTPSExchangeTransport().send(wire)
                self.assertEqual(result.status,200)
                sent,timeout=opener.requests[-1]
                self.assertEqual(sent.full_url,ORIGIN+path)
                self.assertEqual(sent.method,method)
                self.assertEqual(timeout,5)
                self.assertNotIn(KEY,repr(wire))
        with self.assertRaises(ExchangeUnavailable):
            HTTPSExchangeTransport().send(WireRequest('POST','/v1/account/orders',{'x-api-key':KEY,'sub-account-client-id':ACCOUNT,'Content-Type':'application/json','Accept-Encoding':'identity'},b'{}'))
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'','', 'https://foreign.invalid'))
        for path in ('https://foreign.invalid','/v1/account/withdraw','/v1/account/orders?clientOrderId=BUY_1&evil=x'):
            with self.subTest(path=path),self.assertRaises(ExchangeUnavailable):
                HTTPSExchangeTransport().send(WireRequest('POST',path,{},None))
