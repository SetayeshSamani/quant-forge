"""Bounded real-venue evidence collection. GET only; no order activation.

Account data stays in memory. Public output contains counts/categories/hashes,
never keys, account identifiers, private amounts or raw provider responses.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import argparse
import json
import re

from wallex.infra.config import load_settings
from wallex.infra.spot_preflight import SpotGetTransport, _discovered_accounts
from wallex.infra.credit_readonly import HttpGet
from wallex.market.datasets import _unique_object
from wallex.market.public_orderbook import analyze_orderbook
from .models import amount, canonical_json, utc
from .wallex_http import WallexExchange, ExchangeUnavailable, BalanceEvidence, _reject_constant, _number


@dataclass(frozen=True)
class VenueSnapshot:
    account_id: str = field(repr=False)
    symbol: str
    base_asset: str
    quote_asset: str
    observed_at: datetime
    origin: str
    balances: BalanceEvidence = field(repr=False)
    maker_rate: Decimal
    taker_rate: Decimal
    price_step: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    maximum_quantity: Decimal | None
    open_orders: tuple[str, ...] = field(repr=False)
    orders: tuple[str, ...] = field(repr=False)
    trades: tuple[str, ...] = field(repr=False)
    book: dict = field(repr=False)
    source_hashes: tuple[tuple[str, str], ...]


def configured_exchange(settings, *, clock, transport=None, discovery_transport=None):
    if not settings.wallex_api_key or not settings.wallex_subaccount_id:
        raise ExchangeUnavailable("EXPLICIT_CONFIGURED_ACCOUNT_REQUIRED")
    reply=(discovery_transport or SpotGetTransport()).get(HttpGet('/sub-accounts',{'x-api-key':settings.wallex_api_key},5,262144))
    if reply.status!=200 or type(reply.body) is not bytes or len(reply.body)>262144:
        raise ExchangeUnavailable("DISCOVERY_AUTHENTICATION_OR_RESPONSE_FAILED")
    data=json.loads(reply.body,parse_constant=_reject_constant,object_pairs_hook=_unique_object)
    if data.get('success') is not True:raise ExchangeUnavailable("DISCOVERY_RESPONSE_INVALID")
    base,quote=settings.assets
    options=_discovered_accounts(data,settings.wallex_symbol,base,quote)
    matches=[o for o in options if o.sub_account_client_id==settings.wallex_subaccount_id]
    if len(matches)!=1:raise ExchangeUnavailable("CONFIGURED_ACCOUNT_INACTIVE_OR_AMBIGUOUS")
    return WallexExchange.from_settings(settings=settings,owner_user_id=matches[0].owner_user_id,clock=clock,transport=transport)


def _wire_number(value, *, zero=False):
    # json.loads(parse_float=Decimal): no binary float can enter a price calculation.
    if type(value) is int:value=Decimal(value)
    elif type(value) is str:return _number(value,zero=zero)
    return amount(value,zero=zero)


def collect_venue(exchange, *, symbol, base_asset, quote_asset):
    if symbol!=base_asset+quote_asset:raise ExchangeUnavailable("MARKET_IDENTITY_MISMATCH")
    started=utc(exchange.clock());hashes=[]
    def read(path, label):
        if not 0<=(utc(exchange.clock())-started).total_seconds()<=30:
            raise ExchangeUnavailable("ACCOUNT_CAPTURE_TIME_BUDGET")
        data=exchange._read('GET',path,envelope=True)
        hashes.append((label,exchange.last_hash))
        return data
    exchange.verify_scope();hashes.append(('scope',exchange.last_hash))
    balances=exchange.balances();hashes.append(('balances',balances.raw_sha256))
    fee=read('/v1/account/fee','fees')['result']
    row=fee.get(symbol) if type(fee) is dict else None
    if type(row) is not dict:raise ExchangeUnavailable("SYMBOL_FEES_UNAVAILABLE")
    maker,taker=(_number(row.get(k),zero=True) for k in ('makerFeeRate','takerFeeRate'))
    if maker>=1 or taker>=1:raise ExchangeUnavailable("FEE_RATE_INVALID")
    markets=read('/v1/markets','market_rules')['result']
    rules=markets.get('symbols',{}).get(symbol) if type(markets) is dict else None
    if (type(rules) is not dict or (rules.get('symbol'),rules.get('baseAsset'),rules.get('quoteAsset'))!=(symbol,base_asset,quote_asset)
            or type(rules.get('isMarketTypeEnable')) is not bool):raise ExchangeUnavailable("MARKET_IDENTITY_OR_MARKET_TYPE_FLAG_UNVERIFIED")
    # isMarketTypeEnable refers to MARKET orders, not prop permission or LIMIT eligibility.
    # Official v1/markets stepSize/tickSize are decimal-place COUNTS, not increments.
    digits=[rules.get(k) for k in ('tickSize','stepSize')]
    if any(type(v) is not int or not 0<=v<=18 for v in digits):raise ExchangeUnavailable("INSTRUMENT_PRECISION_INVALID")
    price_step,quantity_step=(Decimal((0,(1,),-v)) for v in digits)
    minq,minn=(_wire_number(rules.get(k)) for k in ('minQty','minNotional'))
    maxq=_wire_number(rules['maxQty']) if 'maxQty' in rules else None
    if maxq is not None and maxq<minq:raise ExchangeUnavailable("INSTRUMENT_LIMITS_INCONSISTENT")
    def pages(kind, sweep):
        rows=[];identities=set()
        for page in range(1,4):
            data=read('/v1/account/'+kind+'?page='+str(page)+'&per_page=100',kind+'_'+str(sweep)+'_'+str(page))
            info,result=data.get('result_info'),data.get('result')
            if (type(info) is not dict or type(info.get('page')) is not int or info['page']!=page
                    or type(info.get('per_page')) is not int or (not 0<=info['per_page']<=100 if kind=='openOrders' else info['per_page']!=100)
                    or (kind!='openOrders' and type(info.get('has_more_pages')) is not bool)):
                raise ExchangeUnavailable("PROVIDER_PAGE_TERMINATION_UNVERIFIED_"+kind.upper())
            if type(result) is not dict:raise ExchangeUnavailable("ACCOUNT_PAGE_MALFORMED")
            if kind=='trades':batch=result.get('AccountLatestTrades')
            else:
                if any(type(v) is not list for v in result.values()):raise ExchangeUnavailable("ACCOUNT_PAGE_MALFORMED")
                batch=[v for group in result.values() for v in group]
            if type(batch) is not list or len(batch)>100 or (not batch and info.get('has_more_pages') is True):
                raise ExchangeUnavailable("CONTRADICTORY_ACCOUNT_PAGINATION")
            for row in batch:
                if type(row) is not dict:raise ExchangeUnavailable("ACCOUNT_ROW_MALFORMED")
                if kind!='trades':
                    identity=row.get('clientOrderId')
                    if type(identity) is not str or not identity or identity in identities:
                        raise ExchangeUnavailable("ACCOUNT_ORDER_IDENTITIES_AMBIGUOUS")
                    if type(row.get('active')) is not bool or (kind=='openOrders' and row['active'] is not True):
                        raise ExchangeUnavailable("ACCOUNT_ORDER_STATUS_UNVERIFIED")
                    if row.get('side') not in ('BUY','SELL') or type(row.get('symbol')) is not str:
                        raise ExchangeUnavailable("ACCOUNT_ORDER_IDENTITY_UNVERIFIED")
                    identities.add(identity)
                else:
                    if type(row.get('isBuyer')) is not bool or type(row.get('symbol')) is not str:
                        raise ExchangeUnavailable("ACCOUNT_TRADE_IDENTITY_UNVERIFIED")
                    for k in ('price','quantity','sum','fee'):_number(row.get(k),zero=k=='fee')
                    # AccountLatestTrades does not document a stable per-order trade ID.
                rows.append(canonical_json(row))
            if kind=='openOrders' and 'has_more_pages' not in info:
                # An openOrders page alone proves no completeness. The complete
                # all-orders history below must independently match its active IDs.
                return tuple(sorted(rows))
            if info.get('has_more_pages') is False:return tuple(sorted(rows))
        raise ExchangeUnavailable("ACCOUNT_PAGINATION_BUDGET_EXHAUSTED")
    captured=[]
    for kind in ('openOrders','orders','trades'):
        first,second=pages(kind,1),pages(kind,2)
        if first!=second:raise ExchangeUnavailable("ACCOUNT_CHANGED_DURING_DOUBLE_CAPTURE")
        captured.append(first)
    active={json.loads(r)['clientOrderId'] for r in captured[0]}
    history={json.loads(r)['clientOrderId'] for r in captured[1] if json.loads(r)['active']}
    if active!=history:raise ExchangeUnavailable("OPEN_AND_ALL_ORDERS_INCONSISTENT")
    payload=read('/v1/depth?symbol='+symbol,'depth');rawhash=exchange.last_hash
    book=analyze_orderbook(canonical_json(payload).encode('ascii'),symbol=symbol,received_at=int(utc(exchange.clock()).timestamp()))
    book['raw_sha256']=rawhash  # Original wire hash, not the Decimal re-encoding used for validation.
    if not book['valid']:raise ExchangeUnavailable("PUBLIC_BOOK_INVALID")
    ended=utc(exchange.clock())
    if not 0<=(ended-started).total_seconds()<=30:raise ExchangeUnavailable("ACCOUNT_CAPTURE_STALE")
    return VenueSnapshot(exchange.account_id,symbol,base_asset,quote_asset,ended,
        'HTTPS' if exchange.actual_https else 'HTTP_MOCK',balances,maker,taker,price_step,quantity_step,minq,minn,maxq,
        *captured,book,tuple(hashes))


def readiness_report(capture):
    blockers=['TRADE_PERMISSION_NOT_EXPOSED_BY_DOCUMENTED_GET','CREDIT_DEBT_COLLATERAL_EXPIRY_UNVERIFIED',
        'FEE_DEBIT_ASSET_POLICY_UNVERIFIED','PUBLIC_BOOK_PROVIDER_EVENT_TIME_UNAVAILABLE',
        'VERIFIED_RISK_EQUITY_AND_PERSISTENT_LOSS_BASELINE_REQUIRED','ORDER_SPECIFIC_OWNER_APPROVAL_REQUIRED']
    if capture.maximum_quantity is None:blockers.append('VENUE_MAX_QUANTITY_NOT_RETURNED')
    if capture.open_orders:blockers.append('EXISTING_OPEN_ORDERS_BLOCK_ENTRY')
    if capture.balances.row(capture.base_asset)[1]>0:blockers.append('EXISTING_BASE_EXPOSURE_REQUIRES_RECOVERY')
    if any(row[1]>0 for row in capture.balances.assets if row[0] not in (capture.base_asset,capture.quote_asset)):
        blockers.append('OTHER_ASSET_EXPOSURE_REQUIRES_RECONCILIATION')
    if capture.balances.row(capture.quote_asset)[2]<=0:blockers.append('NO_AVAILABLE_QUOTE_FUNDS')
    return {'mode':'EXECUTION_READINESS_GET_ONLY','origin':capture.origin,'account':'DISCOVERY_MATCHED_DOCUMENTED_HEADER_SCOPE',
        'response_account_echo':'UNAVAILABLE','fees':'AUTHENTICATED_MAKER_TAKER_RATES_VERIFIED',
        'market_rules':'PRECISION_MINIMUMS_VERIFIED_MAXIMUM_UNAVAILABLE' if capture.maximum_quantity is None else 'PRECISION_MINIMUMS_MAX_QUANTITY_VERIFIED',
        'coverage':'ALL_ORDERS_TRADES_PROVIDER_END_OBSERVED_DOUBLE_CHECKED_NOT_ATOMIC',
        'open_orders_observed':len(capture.open_orders),'historical_orders_observed':len(capture.orders),'trades_observed':len(capture.trades),
        'quote':'UNCROSSED_LOCAL_RECEIPT_ONLY_NO_SOURCE_EVENT_TIME',
        'observed_at':int(capture.observed_at.timestamp()),'source_hashes':dict(capture.source_hashes),
        'blockers':blockers,'action':'NO_TRADE','executable_ready':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--connect',action='store_true',required=True)
    args=parser.parse_args(argv)
    try:
        settings=load_settings();clock=lambda:datetime.now(timezone.utc)
        exchange=configured_exchange(settings,clock=clock)
        report=readiness_report(collect_venue(exchange,symbol=settings.wallex_symbol,base_asset=settings.assets[0],quote_asset=settings.assets[1]))
        print(canonical_json(report));return 0
    except Exception as error:
        reason=str(error) if isinstance(error,ExchangeUnavailable) and re.fullmatch(r'[A-Z0-9_]{1,100}',str(error)) else 'READINESS_CAPTURE_FAILED_CLOSED'
        print(canonical_json({'status':'BLOCKED','blockers':[reason],'action':'NO_TRADE','executable_ready':False}));return 1


if __name__=='__main__':raise SystemExit(main())
