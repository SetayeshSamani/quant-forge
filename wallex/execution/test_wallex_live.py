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

D=Decimal
KEY='synthetic-execution-key-never-live'
ACCOUNT='synthetic_account'
OWNER='AUTHORIZE WALLEX LIVE '+ACCOUNT


def setup_inputs():
    risk=risk_fixture()
    risk.frame.symbol='BTCTMN';risk.frame.base_asset='BTC';risk.frame.quote_asset='TMN'
    for bar in (*risk.frame.candles_15m,*risk.frame.candles_1h):bar.symbol='BTCTMN'
    risk=replace(risk,account_id=ACCOUNT,quote_asset='TMN',worst_case_extra_loss_per_unit=D(1),
        portfolio_state=replace(risk.portfolio_state,account_id=ACCOUNT,quote_asset='TMN'),
        loss_state=replace(risk.loss_state,quote_asset='TMN'))
    now=datetime.fromtimestamp(risk.frame.as_of,timezone.utc)
    request=synthetic_request(account_id=ACCOUNT)
    request=replace(request,intent=replace(request.intent,intent_id='live_buy',client_id='BUY_1',symbol='BTCTMN',
        base_asset='BTC',quote_asset='TMN',quantity_base=D('.5'),price_quote_per_base=D(110),
        created_at=now,expires_at=now+timedelta(seconds=20),mode=Mode.LIVE),
        reservation=Reservation('TMN',D('55.1'),D(1000)),evidence_kind='live_verified')
    checks=ReplayAdmission(risk,True,True,True,'BTCTMN','BTC','TMN',D(108),D(109),now,30,True,
        D(1),D('.01'),D(1),D(1000),D('.001'),(),True)
    policy=ProtectionPolicy(D("97.6"),D(97),D(0))
    return request,checks,policy,now


class HttpTape:
    def __init__(self,now):
        self.now=now;self.orders={};self.requests=[];self.fault=None;self.status=200;self.cancel_fault=False
        self.base=D(0);self.quote=D(10000)

    def response(self,result,status=200):
        return WireResponse(status,json.dumps({'success':True,'result':result}).encode())

    def send(self,request):
        self.requests.append(request)
        assert request.headers['x-api-key']==KEY
        if request.path!='/sub-accounts':assert request.headers['sub-account-client-id']==ACCOUNT
        if request.path=='/sub-accounts':
            return WireResponse(200,json.dumps({'success':True,'result':[{'clientId':ACCOUNT,'userId':'7','type':'prop','isActive':True,'isSuspend':False}], 'result_info':{'page':1,'count':1,'total_count':1}}).encode())
        if self.status!=200:return WireResponse(self.status,b'{"success":false,"result":{"error_code":1006}}')
        if request.method=='POST':
            body=json.loads(request.body);cid=body['client_id']
            assert cid not in self.orders,'DUPLICATE POST'
            self.orders[cid]={'clientOrderId':cid,'symbol':body['symbol'],'side':body['side'],'type':'LIMIT',
                'origQty':body['quantity'],'price':body['price'],'executedQty':'0','active':True,'status':'NEW','fills':[]}
            if self.fault:
                self.fill(cid,D('.25'));fault,self.fault=self.fault,None
                if fault=='crash':raise KeyboardInterrupt()
                raise TimeoutError()
            return self.response(self.orders[cid],201)
        if request.method=='DELETE':
            cid=request.path.split('clientOrderId=')[1]
            if self.cancel_fault:raise TimeoutError()
            self.orders[cid]['status']='CANCELED';self.orders[cid]['active']=False
            return self.response(self.orders[cid])
        if request.path.startswith('/v1/account/orders/'):
            cid=request.path.rsplit('/',1)[1]
            return self.response(deepcopy(self.orders[cid])) if cid in self.orders else WireResponse(404,b'{}')
        if request.path.endswith('/balances-detail'):
            return self.response({asset:{'symbol':asset,'total':str(v),'available':str(v),'freeze':'0'} for asset,v in (('BTC',self.base),('TMN',self.quote))})
        if '/openOrders' in request.path:
            return self.response({'orders':[deepcopy(o) for o in self.orders.values() if o['active']]})
        if '/trades' in request.path:
            return self.response({'AccountLatestTrades':[deepcopy(f) for o in self.orders.values() for f in o['fills']]})
        raise AssertionError('Unexpected endpoint')

    def fill(self,cid,quantity):
        row=self.orders[cid];price=D(row['price']);fee=quantity*price*D('.001');buy=row['side']=='BUY'
        row['fills'].append({'symbol':row['symbol'],'quantity':str(quantity),'price':str(price),'sum':str(quantity*price),
            'fee':str(fee),'feeAsset':'TMN','isBuyer':buy,'timestamp':self.now.isoformat()})
        total=D(row['executedQty'])+quantity;row['executedQty']=str(total)
        row['active']=total<D(row['origQty']);row['status']='NEW' if row['active'] else 'FILLED'
        self.base+=quantity if buy else -quantity
        self.quote+=(-quantity*price-fee) if buy else quantity*price-fee


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
        gateway.authorize_exit(request.intent.intent_id,sell,e,owner_confirmation=OWNER)
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
            gateway.authorize_entry(request,e,policy,owner_confirmation=OWNER)
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
                e=self.evidence(exchange,checks);gateway.authorize_entry(request,e,policy,owner_confirmation=OWNER)
                tape.fault=fault
                if fault=='crash':
                    with self.assertRaises(KeyboardInterrupt):gateway.submit(request)
                    self.assertEqual(ledger.get('live_buy').state,OrderState.SUBMITTING)
                else:self.assertEqual(gateway.submit(request).receipt.state,OrderState.UNKNOWN)
                new_exchange=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
                restarted=WallexGateway(Ledger(ledger.path,mode=Mode.LIVE),new_exchange,clock=lambda:now)
                recovered=restarted.recover_account(ACCOUNT,run_id='recover',trace_id='recover')
                self.assertEqual(recovered[0].state,OrderState.PARTIAL)
                restarted.authorize_entry(request,e,policy,owner_confirmation=OWNER)
                self.assertEqual(restarted.submit(request).disposition,'existing_no_resubmission')
                self.assertEqual(len([r for r in tape.requests if r.method=='POST']),1)
                self.assertEqual(restarted.cancel('live_buy',evidence=self.evidence(new_exchange,checks),owner_confirmation=OWNER).state,OrderState.CANCELED)
                self.close_position(request,checks,now,tape,new_exchange,ledger,restarted)
                self.assertEqual(tape.quote,D('9996.69825'))

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
                with self.subTest(index=index),self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,bad,policy,owner_confirmation=OWNER)
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,policy)
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,replace(policy,initial_stop=D(100)),owner_confirmation=OWNER)
            with self.assertRaises(ExecutionRefused):gateway.authorize_entry(request,e,replace(policy,minimum_sell_price=D(90)),owner_confirmation=OWNER)
            with self.assertRaises(ExchangeUnavailable):exchange.submit(request.intent)
            real=WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,clock=lambda:now)
            real_gate=WallexGateway(ledger,real,clock=lambda:now)
            self.assertIn('MOCK_OR_UNVERIFIED_EVIDENCE_ON_REAL_TRANSPORT',real_gate.readiness(request,e))
            self.assertIn('RISK_PIPELINE_DIAGNOSTIC_ONLY_NOT_LIVE_AUTHORITY',real_gate.readiness(request,replace(e,origin='LIVE_VERIFIED')))
            self.assertFalse(any(r.method in ('POST','DELETE') for r in tape.requests))
            with self.assertRaises(LedgerError):Ledger(ledger.path) # no silent live/replay migration.

    def test_http_errors_malformed_fills_cancel_uncertainty_and_balance_mismatch_hold(self):
        for status in (401,403,404,429,500,422):
            with self.subTest(status=status),TemporaryDirectory() as directory:
                request,checks,policy,now,tape,exchange,ledger,gateway=self.make(directory)
                gateway.authorize_entry(request,self.evidence(exchange,checks),policy,owner_confirmation=OWNER)
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
                gateway.authorize_entry(request,self.evidence(exchange,checks),policy,owner_confirmation=OWNER)
                gateway.submit(request);tape.fill('BUY_1',D('.25'))
                if case=='cancel':
                    gateway.reconcile('live_buy');tape.cancel_fault=True
                    self.assertEqual(gateway.cancel('live_buy',evidence=self.evidence(exchange,checks),owner_confirmation=OWNER).state,OrderState.UNKNOWN)
                elif case=='balance':
                    gateway.reconcile('live_buy');gateway.cancel('live_buy',evidence=self.evidence(exchange,checks),owner_confirmation=OWNER)
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
                ('DELETE','/v1/account/orders?clientOrderId=BUY_1',None)):
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
