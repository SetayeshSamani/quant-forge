"""Bounded GET evidence tests, entirely synthetic HTTP; never real keys."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from tempfile import TemporaryDirectory
import unittest

from .test_wallex_live import HttpTape,setup_inputs,KEY,ACCOUNT
from .wallex_http import WallexExchange,ExchangeUnavailable
from .preflight import collect_venue,readiness_report


class ReadTape(HttpTape):
    def __init__(self,now,case=None):super().__init__(now);self.case=case;self.hits={}
    def send(self,wire):
        self.hits[wire.path]=self.hits.get(wire.path,0)+1
        if wire.path=='/v1/account/fee':
            return self.response({'BTCTMN':{'makerFeeRate':'0.001','takerFeeRate':'0.002'}})
        if wire.path=='/v1/markets':
            assert 'x-api-key' not in wire.headers and 'sub-account-client-id' not in wire.headers
            return self.response({'symbols':{'BTCTMN':{'symbol':'BTCTMN','baseAsset':'BTC','quoteAsset':'TMN',
                'tickSize':0,'stepSize':2,'minQty':'0.01','minNotional':1,'isMarketTypeEnable':True}}})
        if wire.path.startswith('/v1/depth?'):
            assert 'x-api-key' not in wire.headers
            return self.response({'bid':[{'price':'108','quantity':'1','sum':'108'}],
                'ask':[{'price':'109','quantity':'1','sum':'109'}]})
        if '?page=' in wire.path:
            kind=wire.path.split('?')[0].rsplit('/',1)[1]
            info={'page':1,'per_page':0,'count':0} if kind=='openOrders' else {'page':1,'per_page':100,'has_more_pages':False}
            rows=[]
            if kind=='orders' and self.case=='missing_end':info.pop('has_more_pages')
            if kind=='orders' and self.case=='changing' and self.hits[wire.path]==2:
                rows=[{'clientOrderId':'synthetic_order','active':False,'side':'BUY','symbol':'BTCTMN'}]
            if kind=='orders' and self.case=='empty_more':info['has_more_pages']=True
            result={'AccountLatestTrades':rows} if kind=='trades' else {'orders':rows}
            response=self.response(result)
            payload=json.loads(response.body);payload['result_info']=info
            return replace(response,body=json.dumps(payload).encode())
        return super().send(wire)


class ExecutionReadinessTests(unittest.TestCase):
    def exchange(self,case=None):
        _,_,_,now=setup_inputs();tape=ReadTape(now,case)
        return WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now),tape
    def test_real_wire_contract_shapes_have_bounded_get_coverage_and_no_permission_inference(self):
        exchange,tape=self.exchange()
        capture=collect_venue(exchange,symbol='BTCTMN',base_asset='BTC',quote_asset='TMN')
        self.assertEqual(capture.price_step,Decimal(1))
        self.assertEqual(capture.quantity_step,Decimal('.01')) # stepSize is decimal digits, not increment=2.
        self.assertEqual(capture.taker_rate,Decimal('.002'))
        report=readiness_report(capture)
        self.assertEqual(report['origin'],'HTTP_MOCK')
        self.assertIn('TRADE_PERMISSION_NOT_EXPOSED_BY_DOCUMENTED_GET',report['blockers'])
        self.assertIn('PUBLIC_BOOK_PROVIDER_EVENT_TIME_UNAVAILABLE',report['blockers'])
        self.assertFalse(report['executable_ready'])
        self.assertNotIn(ACCOUNT,json.dumps(report));self.assertNotIn(KEY,json.dumps(report))
        self.assertTrue(all(v<=2 for v in tape.hits.values()))
    def test_missing_termination_changing_state_and_empty_continuation_fail_closed(self):
        for case in ('missing_end','changing','empty_more'):
            with self.subTest(case=case):
                exchange,_=self.exchange(case)
                with self.assertRaises(ExchangeUnavailable):collect_venue(exchange,symbol='BTCTMN',base_asset='BTC',quote_asset='TMN')
