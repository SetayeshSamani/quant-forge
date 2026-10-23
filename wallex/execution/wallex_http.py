"""Fixed-origin scoped Wallex REST transport. No ambient key, retries or logging.

Wire references: https://api-docs.wallex.ir/ and
https://developers.wallex.ir/docs/spot-intro /spot-create-order /spot-single-order-detail
Cancellation uses the documented reference query clientOrderId; no endpoint fallback.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from wallex.market.datasets import _unique_object
from wallex.infra.credit_readonly import _contains
from wallex.market.history import _NoRedirect, _reject_constant
from .models import (Fill, Mode, OrderObservation, OrderState, Side, amount,
                     canonical_json, decimal_text, exact_product, exact_sum, utc)

ORIGIN = "https://api.wallex.ir"
MAX_BYTES = 524288


class ExchangeUnavailable(RuntimeError):
    pass


class _SubmissionRejected(ExchangeUnavailable):
    pass


@dataclass(frozen=True)
class WireRequest:
    method: str
    path: str = field(repr=False)
    headers: dict = field(repr=False)
    body: bytes | None = field(repr=False)
    timeout: int = 5
    permit: object = field(default=None, repr=False)


@dataclass(frozen=True)
class WireResponse:
    status: int
    body: bytes = field(repr=False)


def _allowed(method, path):
    return ((method == "POST" and path == "/v1/account/orders")
        or (method == "DELETE" and re.fullmatch(r"/v1/account/orders\?clientOrderId=[A-Za-z0-9_]{1,64}", path))
        or (method == "GET" and (re.fullmatch(r"/v1/account/orders/[A-Za-z0-9_]{1,64}", path)
            or re.fullmatch(r"/v1/account/(openOrders|trades)\?symbol=[A-Z0-9]{3,30}", path)
            or path in ("/v1/account/balances-detail", "/sub-accounts"))))


class HTTPSExchangeTransport:
    """No proxy/redirect/retry; socket timeout and bytes bounded, TLS verified."""
    def send(self, request):
        if (type(request) is not WireRequest or not _allowed(request.method, request.path)
                or request.timeout != 5 or set(request.headers) != ({"x-api-key", "Content-Type", "Accept-Encoding"} if request.path=="/sub-accounts" else {"x-api-key", "sub-account-client-id", "Content-Type", "Accept-Encoding"})):
            raise ExchangeUnavailable("INVALID_HTTP_REQUEST")
        if request.method in ("POST","DELETE"):
            from .live import _WritePermit
            permit=request.permit
            if (type(permit) is not _WritePermit or permit.account_id!=request.headers["sub-account-client-id"]
                    or not 0<(permit.expires_at-datetime.now(timezone.utc)).total_seconds()<=5
                    or permit.operation!=("SUBMIT" if request.method=="POST" else "CANCEL")):
                raise ExchangeUnavailable("HTTPS_WRITE_NOT_AUTHORIZED")
            if request.method=="POST":
                try:
                    body=json.loads(request.body,object_pairs_hook=_unique_object,parse_constant=_reject_constant)
                    if body.get("client_id")!=permit.client_id:raise ValueError()
                except Exception:
                    raise ExchangeUnavailable("PERMIT_ORDER_MISMATCH") from None
            elif request.path!="/v1/account/orders?clientOrderId="+permit.client_id:
                raise ExchangeUnavailable("PERMIT_ORDER_MISMATCH")
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        wire = Request(ORIGIN+request.path, method=request.method, headers=request.headers, data=request.body)
        try:
            with opener.open(wire, timeout=5) as response:
                return WireResponse(response.status, response.read(MAX_BYTES+1))
        except HTTPError as error:
            try:
                return WireResponse(error.code, error.read(MAX_BYTES+1))
            finally:
                error.close()
        except Exception:
            raise ExchangeUnavailable("HTTP_OUTCOME_UNKNOWN") from None


@dataclass(frozen=True)
class BalanceEvidence:
    account_id: str = field(repr=False)
    observed_at: datetime
    raw_sha256: str
    assets: tuple = field(repr=False)  # asset, total, available, frozen; never equity.

    def row(self, asset):
        found = [r for r in self.assets if r[0] == asset]
        if len(found) != 1:
            raise ExchangeUnavailable("BALANCE_ASSET_UNAVAILABLE")
        return found[0]


def _number(value, *, zero=False):
    if type(value) is not str or len(value) > 200 or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?",value) is None:
        raise ExchangeUnavailable("INVALID_NUMERIC_WIRE_TYPE")
    try:
        return amount(Decimal(value), zero=zero)
    except Exception:
        raise ExchangeUnavailable("INVALID_WIRE_NUMBER") from None


def _time(value):
    if type(value) is not str or len(value) > 50:
        raise ExchangeUnavailable("UNVERIFIED_FILL_TIMESTAMP")
    try:
        return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        raise ExchangeUnavailable("UNVERIFIED_FILL_TIMESTAMP") from None


class WallexExchange:
    """Only gateway-issued one-operation permits can authorize POST/DELETE.

    Returned order fills are a COMPLETE per-order snapshot, checked against
    executedQty. Local IDs hash economic content plus identical-row occurrence;
    they are explicitly NOT provider trade IDs. AccountLatestTrades is not used
    to guess order attribution. Missing/corrected snapshots fail UNKNOWN upstream.
    """
    def __init__(self, *, api_key, account_id, owner_user_id, transport=None, clock):
        if (type(api_key) is not str or not 1 <= len(api_key) <= 4096
                or not all(33 <= ord(c) <= 126 for c in api_key)
                or type(owner_user_id) is not int or not 0<owner_user_id<10**18
                or type(account_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,200}", account_id) is None):
            raise ExchangeUnavailable("EXPLICIT_SCOPED_CREDENTIAL_REQUIRED")
        self._key, self.account_id, self._owner_user_id = api_key, account_id, owner_user_id
        self._transport, self.clock = transport or HTTPSExchangeTransport(), clock
        self._intents, self._permit, self.last_hash = {}, None, None
        self.last_http_status, self.last_failure_code = None, None

    @classmethod
    def from_settings(cls, *, owner_user_id, clock, settings=None, transport=None):
        from wallex.infra.config import load_settings, ConfigurationError
        settings=load_settings() if settings is None else settings
        if not settings.wallex_api_key:raise ConfigurationError("WALLEX_API_KEY_MISSING")
        if not settings.wallex_subaccount_id:raise ConfigurationError("WALLEX_SUBACCOUNT_ID_REQUIRED")
        return cls(api_key=settings.wallex_api_key,account_id=settings.wallex_subaccount_id,
                   owner_user_id=owner_user_id,transport=transport,clock=clock)

    @property
    def actual_https(self):
        return type(self._transport) is HTTPSExchangeTransport

    def bind(self, intent):
        if (intent.mode is not Mode.LIVE or intent.account_id != self.account_id
                or re.fullmatch(r"[A-Za-z0-9_]{1,64}", intent.client_id) is None
                or re.fullmatch(r"[A-Z0-9]{3,30}", intent.symbol) is None
                or intent.symbol != intent.base_asset+intent.quote_asset):
            raise ExchangeUnavailable("ORDER_SCOPE_OR_ID_INVALID")
        previous = self._intents.get(intent.client_id)
        if previous is not None and previous != intent:
            raise ExchangeUnavailable("CLIENT_ID_REUSE")
        self._intents[intent.client_id] = intent

    def _read(self, method, path, body=None, *, envelope=False, permit=None):
        if not _allowed(method, path):
            raise ExchangeUnavailable("ENDPOINT_NOT_ALLOWED")
        self.last_http_status, self.last_failure_code = None, None
        headers={"x-api-key":self._key,"Content-Type":"application/json","Accept-Encoding":"identity"}
        if path!="/sub-accounts":headers["sub-account-client-id"]=self.account_id
        request = WireRequest(method,path,headers,body,permit=permit)
        try:
            response = self._transport.send(request)
        except Exception:
            self.last_failure_code="HTTP_OUTCOME_UNKNOWN"
            raise ExchangeUnavailable("HTTP_OUTCOME_UNKNOWN") from None
        if (type(response) is not WireResponse or type(response.status) is not int
                or type(response.body) is not bytes or len(response.body) > MAX_BYTES):
            raise ExchangeUnavailable("INVALID_HTTP_RESPONSE")
        self.last_http_status=response.status
        self.last_hash = sha256(response.body).hexdigest()
        if method=="POST" and response.status==422:
            try:
                rejected=json.loads(response.body,object_pairs_hook=_unique_object,parse_constant=_reject_constant)
                if (rejected.get("success") is False and type(rejected["result"].get("error_code")) is int
                        and rejected["result"]["error_code"] in range(1001,1012)):
                    self.last_failure_code="VENUE_VALIDATION_REJECTED"
                    raise _SubmissionRejected("VENUE_VALIDATION_REJECTED")
            except _SubmissionRejected:
                raise
            except Exception:
                pass
        if response.status != (201 if method=="POST" else 200):
            code = {401:"AUTHENTICATION_FAILED",403:"FORBIDDEN",404:"ORDER_NOT_FOUND_UNCERTAIN",429:"RATE_LIMITED"}.get(response.status,"HTTP_REJECTED_OR_UNKNOWN")
            self.last_failure_code=code
            raise ExchangeUnavailable(code)
        try:
            payload = json.loads(response.body, parse_float=Decimal, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            if type(payload) is not dict or payload.get("success") is not True or _contains(payload,self._key):
                raise ValueError()
            return payload if envelope else payload["result"]
        except Exception:
            raise ExchangeUnavailable("MALFORMED_EXCHANGE_RESPONSE") from None

    def _consume_permit(self, client_id, operation):
        from .live import _WritePermit
        permit, self._permit = self._permit, None
        if (type(permit) is not _WritePermit or permit.account_id != self.account_id
                or permit.client_id != client_id or permit.operation != operation
                or utc(self.clock()) >= permit.expires_at):
            raise ExchangeUnavailable("LIVE_WRITE_NOT_AUTHORIZED")
        return permit

    def submit(self, intent):
        self.bind(intent)
        permit=self._consume_permit(intent.client_id, "SUBMIT")
        body = json.dumps({"symbol":intent.symbol,"side":intent.side.value,"type":"LIMIT",
            "price":decimal_text(intent.price_quote_per_base),"quantity":decimal_text(intent.quantity_base),
            "client_id":intent.client_id},separators=(",", ":")).encode("ascii")
        try:
            result = self._read("POST", "/v1/account/orders", body, permit=permit)
        except _SubmissionRejected:
            return OrderObservation(intent.account_id,intent.client_id,intent.symbol,"wallex_order_fill_snapshot",
                None,OrderState.REJECTED,Decimal(0),(),utc(self.clock()),None,"venue_validation_rejection")
        return self._observation(intent, result)

    def lookup(self, account_id, client_id):
        if account_id != self.account_id or client_id not in self._intents:
            raise ExchangeUnavailable("UNBOUND_ORDER_SCOPE")
        return self._observation(self._intents[client_id], self._read("GET", "/v1/account/orders/"+client_id))

    def cancel(self, account_id, client_id):
        if account_id != self.account_id or client_id not in self._intents:
            raise ExchangeUnavailable("UNBOUND_ORDER_SCOPE")
        permit=self._consume_permit(client_id, "CANCEL")
        self._read("DELETE", "/v1/account/orders?"+urlencode({"clientOrderId":client_id}),permit=permit)
        return self.lookup(account_id, client_id)  # DELETE acknowledgement alone never proves cancellation.

    def _observation(self, intent, row):
        if (type(row) is not dict or (row.get("clientOrderId"),row.get("symbol"),row.get("side"),row.get("type")) !=
                (intent.client_id,intent.symbol,intent.side.value,"LIMIT")
                or _number(row.get("origQty")) != intent.quantity_base
                or _number(row.get("price")) != intent.price_quote_per_base):
            raise ExchangeUnavailable("ORDER_RESPONSE_IDENTITY_MISMATCH")
        filled = _number(row.get("executedQty"),zero=True)
        if filled > intent.quantity_base or type(row.get("active")) is not bool:
            raise ExchangeUnavailable("CONTRADICTORY_ORDER_RESPONSE")
        status = row.get("status")
        if status == "NEW" and row["active"] and filled < intent.quantity_base:
            state = OrderState.PARTIAL if filled else OrderState.WORKING
        elif status in ("FILLED", "FINISHED") and not row["active"] and filled == intent.quantity_base:
            state = OrderState.FILLED
        elif status == "CANCELED" and not row["active"] and filled < intent.quantity_base:
            state = OrderState.CANCELED
        elif status == "REJECTED" and not row["active"] and not filled:
            state = OrderState.REJECTED
        else:
            raise ExchangeUnavailable("UNKNOWN_OR_CONTRADICTORY_ORDER_STATUS")
        rows = row.get("fills", [] if filled == 0 else None)
        if type(rows) is not list or len(rows) > 1000:
            raise ExchangeUnavailable("COMPLETE_ORDER_FILLS_REQUIRED")
        now, occurrences, fills = utc(self.clock()), Counter(), []
        for wire in rows:
            if (type(wire) is not dict or wire.get("symbol") != intent.symbol
                    or wire.get("isBuyer") is not (intent.side is Side.BUY)
                    or wire.get("feeAsset") not in (intent.base_asset,intent.quote_asset)):
                raise ExchangeUnavailable("FILL_IDENTITY_OR_FEE_UNVERIFIED")
            quantity, price, fee = _number(wire.get("quantity")), _number(wire.get("price")), _number(wire.get("fee"),zero=True)
            if _number(wire.get("sum")) != exact_product(quantity,price):
                raise ExchangeUnavailable("FILL_SUM_MISMATCH")
            at = _time(wire.get("timestamp"))
            if not intent.created_at <= at <= now:
                raise ExchangeUnavailable("FILL_TIME_OUTSIDE_ORDER_LIFETIME")
            economic = {"q":quantity,"p":price,"fee":fee,"asset":wire["feeAsset"],"at":at}
            digest = sha256(canonical_json(economic).encode("ascii")).hexdigest()
            occurrences[digest] += 1
            fill_id = intent.client_id+":"+digest+":"+str(occurrences[digest])
            fills.append(Fill("wallex_order_fill_snapshot",fill_id,intent.client_id,intent.account_id,intent.client_id,
                intent.symbol,intent.base_asset,intent.quote_asset,quantity,price,fee,wire["feeAsset"],None,at,None,now))
        if exact_sum(f.quantity_base for f in fills) != filled:
            raise ExchangeUnavailable("EXECUTED_QUANTITY_WITHOUT_COMPLETE_FILLS")
        return OrderObservation(intent.account_id,intent.client_id,intent.symbol,"wallex_order_fill_snapshot",
            intent.client_id,state,filled,tuple(fills),now,None,"order_event_timestamp_not_verified")

    def verify_scope(self):
        from wallex.infra.credit_readonly import _scope_checks, ReadOnlyConfig
        payload=self._read("GET","/sub-accounts",envelope=True)
        try:
            checks=_scope_checks(payload,SimpleNamespace(sub_account_client_id=self.account_id,owner_user_id=self._owner_user_id),ReadOnlyConfig("hidden"))
            if (checks["client_id_match"]!="UNIQUE" or checks["duplicate_client_ids"] or checks["user_id_match"] is not True
                    or checks["account_type"]!="prop" or checks["is_active"] is not True or checks["is_suspended"] is not False):
                raise ValueError()
        except Exception:
            raise ExchangeUnavailable("ACCOUNT_SCOPE_INACTIVE_AMBIGUOUS_OR_MISMATCHED") from None
        return True

    def balances(self):
        rows = self._read("GET", "/v1/account/balances-detail")
        if type(rows) is not dict or not 1 <= len(rows) <= 300:
            raise ExchangeUnavailable("INVALID_BALANCE_RESPONSE")
        assets=[]
        for asset,row in sorted(rows.items()):
            if type(row) is not dict or row.get("symbol") != asset or re.fullmatch(r"[A-Z0-9]{1,20}",asset) is None:
                raise ExchangeUnavailable("BALANCE_IDENTITY_MISMATCH")
            total,available,frozen=(_number(row.get(k),zero=True) for k in ("total","available","freeze"))
            if exact_sum((available,frozen)) != total:
                raise ExchangeUnavailable("INCONSISTENT_BALANCE")
            assets.append((asset,total,available,frozen))
        return BalanceEvidence(self.account_id,utc(self.clock()),self.last_hash,tuple(assets))

    def open_orders(self, symbol):
        if re.fullmatch(r"[A-Z0-9]{3,30}",symbol or "") is None:
            raise ExchangeUnavailable("INVALID_SYMBOL")
        result = self._read("GET", "/v1/account/openOrders?"+urlencode({"symbol":symbol}))
        if type(result) is not dict or not 1<=len(result)<=300 or any(type(v) is not list for v in result.values()):
            raise ExchangeUnavailable("INVALID_OPEN_ORDERS")
        rows=[r for group in result.values() for r in group]
        if len(rows)>1000 or any(type(r) is not dict or r.get("symbol") != symbol or r.get("active") is not True
                or r.get("side") not in ("BUY","SELL") or type(r.get("clientOrderId")) is not str for r in rows):
            raise ExchangeUnavailable("INVALID_OPEN_ORDERS")
        ids=[r["clientOrderId"] for r in rows]
        if len(set(ids)) != len(ids):raise ExchangeUnavailable("DUPLICATE_ORDER_EVIDENCE")
        return tuple(rows)  # Bounded observation, NEVER proof of complete account coverage.

    def trades(self,symbol):
        if re.fullmatch(r"[A-Z0-9]{3,30}",symbol or "") is None:
            raise ExchangeUnavailable("INVALID_SYMBOL")
        result=self._read("GET","/v1/account/trades?"+urlencode({"symbol":symbol}))
        rows=result.get("AccountLatestTrades") if type(result) is dict else None
        if type(rows) is not list or len(rows)>1000:
            raise ExchangeUnavailable("INVALID_TRADE_HISTORY")
        for row in rows:
            if type(row) is not dict or row.get("symbol")!=symbol or type(row.get("isBuyer")) is not bool:
                raise ExchangeUnavailable("INVALID_TRADE_HISTORY")
            for key in ("price","quantity","sum","fee"):_number(row.get(key),zero=key=="fee")
            if _number(row.get("sum"))!=exact_product(_number(row.get("quantity")),_number(row.get("price"))):
                raise ExchangeUnavailable("TRADE_SUM_MISMATCH")
            if type(row.get("feeAsset")) is not str or re.fullmatch(r"[A-Z0-9]{1,20}",row["feeAsset"]) is None:
                raise ExchangeUnavailable("TRADE_FEE_ASSET_UNVERIFIED")
            _time(row.get("timestamp"))
        return tuple(rows)  # No unique fill/order IDs: cannot infer order allocation or completeness.
