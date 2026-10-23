"""Explicit synthetic HTTP tape for the real gateway's offline demo/tests.
No network transport, real credentials or account evidence is used.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from wallex.risk.fixtures import synthetic_pipeline_inputs as risk_fixture
from .fixtures import synthetic_request
from .models import Mode,Reservation,Side
from .live import ProtectionPolicy
from .admission import ReplayAdmission
from .wallex_http import WireResponse

D=Decimal
KEY='synthetic-execution-key-never-live'
ACCOUNT='synthetic_account'
def owner(request,operation='SUBMIT'):return 'AUTHORIZE WALLEX '+operation+' '+request.fingerprint


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
    policy=ProtectionPolicy(D("97.6"),D(97),D(0),30)
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
            cid=request.path.rsplit('/',1)[1]
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
