"""Opt-in public depth diagnostics; no approved quote, freshness or execution."""

import argparse
from decimal import Context, Decimal, DecimalException, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, Underflow
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .datasets import _unique_object
from .evaluation import canonical_json
from .history import _NoRedirect

ENDPOINT = "https://api.wallex.ir/v1/depth"
MAX_BYTES = 262144
MAX_LEVELS = 1000
WARNINGS = ("RESEARCH_ONLY", "MARKET_IDENTITY_UNVERIFIED", "UNITS_UNVERIFIED",
            "SOURCE_EVENT_TIME_UNAVAILABLE", "VERIFIED_FRESHNESS_UNAVAILABLE")


class BookError(ValueError):
    """Stable diagnostic code only; never retain provider messages or bodies."""


def _config(symbol, max_levels, timeout_seconds=5):
    if type(symbol) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", symbol) is None:
        raise BookError("INVALID_CALLER_SYMBOL")
    if type(max_levels) is not int or not 1 <= max_levels <= MAX_LEVELS:
        raise BookError("INVALID_LEVEL_LIMIT")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 10:
        raise BookError("INVALID_TIMEOUT")


def _stamp(value):
    if type(value) is not int or value < 0:
        raise BookError("INVALID_LOCAL_RECEIPT_TIME")
    return value


def _report(symbol, received_at=None, payload=None, reason=None):
    return {"mode": "PUBLIC_ORDERBOOK_RESEARCH", "valid": False,
            "caller_symbol": symbol if type(symbol) is str and re.fullmatch(r"[A-Z0-9]{1,30}", symbol) else None,
            "market_identity": "UNVERIFIED", "source_kind": "CALLER_SUPPLIED_BYTES",
            "received_at": received_at, "local_time_unit": "UTC_UNIX_SECONDS",
            "source_event_time": None, "verified_freshness": "UNAVAILABLE",
            "raw_sha256": sha256(payload).hexdigest() if type(payload) is bytes and len(payload) <= MAX_BYTES else None,
            "bytes_read": len(payload) if type(payload) is bytes else None,
            "best_bid": None, "best_ask": None, "midpoint": None, "spread_pct": None,
            "bid_levels": 0, "ask_levels": 0,
            "reasons": ([reason] if reason else []) + list(WARNINGS),
            "action": "NO_TRADE", "executable_ready": False}


def _positive(value, location):
    if type(value) is str:
        if len(value) > 220 or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None:
            raise BookError("INVALID_NUMBER:" + location)
        try:
            value = Decimal(value, context=Context(traps=[InvalidOperation]))
        except DecimalException:
            raise BookError("INVALID_NUMBER:" + location) from None
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise BookError("NON_POSITIVE_OR_NONFINITE:" + location)
    parts = value.as_tuple()
    if len(parts.digits) > 100 or abs(parts.exponent) > 100:
        raise BookError("NUMERIC_RESOURCE_LIMIT:" + location)
    return value


def _reject_constant(value):
    raise BookError("NONFINITE_JSON")


def analyze_orderbook(payload, *, symbol, received_at, max_levels=100):
    """Validate all provided levels without sorting/truncation or unit inference.

    Wire contract: success=true, result.bid/ask lists of price/quantity/sum.
    Numbers are Decimal; provider sum is checked but not assumed exact price*qty.
    Midpoint is exact within bounded inputs; spread is 100*(ask-bid)/mid rounded
    once to 34 digits, half-even, independent of the caller's Decimal context.
    Local receipt time is caller evidence, never provider event time/freshness.
    """
    result = _report(symbol, payload=payload)
    try:
        _config(symbol, max_levels)
        result["received_at"] = _stamp(received_at)
        if type(payload) is not bytes or len(payload) > MAX_BYTES:
            raise BookError("INVALID_RESPONSE_SIZE")
        try:
            data = json.loads(payload.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                              parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except BookError:
            raise
        except (ValueError, DecimalException, RecursionError):
            raise BookError("MALFORMED_JSON") from None
        if type(data) is not dict or data.get("success") is not True or type(data.get("result")) is not dict:
            raise BookError("INVALID_RESPONSE_SHAPE_OR_SUCCESS")
        sides = {}
        for side in ("bid", "ask"):
            levels = data["result"].get(side)
            if type(levels) is not list or not levels:
                raise BookError("EMPTY_OR_INVALID_SIDE:" + side)
            if len(levels) > max_levels:
                raise BookError("LEVEL_LIMIT_EXCEEDED:" + side)
            prices = []
            for index, level in enumerate(levels):
                where = f"{side}[{index}]"
                if type(level) is not dict or not {"price", "quantity", "sum"} <= level.keys():
                    raise BookError("INVALID_LEVEL:" + where)
                values = {key: _positive(level[key], where + "." + key) for key in ("price", "quantity", "sum")}
                price = values["price"]
                if prices and (price >= prices[-1] if side == "bid" else price <= prices[-1]):
                    raise BookError("UNORDERED_OR_DUPLICATE_LEVEL:" + where)
                prices.append(price)
            sides[side] = prices
        bid, ask = sides["bid"][0], sides["ask"][0]
        if bid >= ask:
            raise BookError("CROSSED_OR_LOCKED_BOOK")
        context = Context(prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                          flags=[], traps=[InvalidOperation, DivisionByZero, Overflow, Underflow])
        mid = (Fraction(bid) + Fraction(ask)) / 2
        exact_spread = 200 * (Fraction(ask) - Fraction(bid)) / (Fraction(ask) + Fraction(bid))
        exact_context = context.copy()
        exact_context.prec = 400
        result.update(valid=True, best_bid=bid, best_ask=ask,
                      midpoint=exact_context.divide(Decimal(mid.numerator), Decimal(mid.denominator)),
                      spread_pct=context.divide(Decimal(exact_spread.numerator), Decimal(exact_spread.denominator)),
                      bid_levels=len(sides["bid"]), ask_levels=len(sides["ask"]),
                      reasons=["BOOK_VALIDATED", *WARNINGS])
    except BookError as error:
        result["reasons"].insert(0, str(error))
    return result


def _public_get(url, *, timeout_seconds):
    if type(url) is not str or re.fullmatch(re.escape(ENDPOINT) + r"\?symbol=[A-Z0-9]{1,30}", url) is None:
        raise BookError("PUBLIC_DEPTH_ENDPOINT_ONLY")
    _config(url.removeprefix(ENDPOINT + "?symbol="), MAX_LEVELS, timeout_seconds)
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity"}, method="GET")
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return response.status, response.read(MAX_BYTES + 1) if response.status == 200 else b""
    except HTTPError as error:
        status = error.code
        error.close()
        return status, b""


def fetch_orderbook(*, symbol, fetch=False, timeout_seconds=5, max_levels=100,
                    transport=None, clock=lambda: time.time_ns() // 1000000000):
    """One explicitly authorized public GET; injected transport returns (status, bytes).

    Fixed endpoint, no credentials/proxies/redirects/retries. Only local request
    start/receipt times are observed. HTTP error bodies are never parsed/reported.
    """
    result = _report(symbol)
    result.update(source_kind="PUBLIC_GET", request_started_at=None, http_status=None)
    try:
        if fetch is not True:
            raise BookError("EXPLICIT_FETCH_REQUIRED")
        _config(symbol, max_levels, timeout_seconds)
        result["request_started_at"] = _stamp(clock())
        response = (transport or _public_get)(ENDPOINT + "?" + urlencode({"symbol": symbol}),
                                             timeout_seconds=timeout_seconds)
        result["received_at"] = _stamp(clock())
        if result["received_at"] < result["request_started_at"]:
            raise BookError("LOCAL_CLOCK_REVERSED")
        if (type(response) is not tuple or len(response) != 2 or type(response[0]) is not int
                or not 100 <= response[0] <= 599):
            raise BookError("INVALID_TRANSPORT_RESPONSE")
        status, payload = response
        result["http_status"] = status
        if status != 200:
            raise BookError("HTTP_" + str(status))
        result.update(analyze_orderbook(payload, symbol=symbol, received_at=result["received_at"], max_levels=max_levels))
        result["source_kind"] = "PUBLIC_GET"
    except BookError as error:
        result["reasons"].insert(0, str(error))
    except (TimeoutError, URLError, OSError) as error:
        timeout = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
        result["reasons"].insert(0, "NETWORK_TIMEOUT" if timeout else "NETWORK_ERROR")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--analyze", action="store_true")
    mode.add_argument("--fetch", action="store_true")
    parser.add_argument("--symbol", required=True, help="Caller identity claim, e.g. BTCTMN")
    parser.add_argument("--input", help="Saved raw UTF-8 JSON response")
    parser.add_argument("--received-at", type=int, help="Recorded local receipt UTC Unix seconds, not file mtime")
    parser.add_argument("--timeout-seconds", type=int, default=5)
    parser.add_argument("--max-levels", type=int, default=100, help="Reject over-limit sides; maximum 1000")
    args = parser.parse_args(argv)
    if args.analyze:
        if args.input is None or args.received_at is None:
            parser.error("--analyze requires --input and --received-at")
        try:
            with Path(args.input).open("rb") as source:
                payload = source.read(MAX_BYTES + 1)
            report = analyze_orderbook(payload, symbol=args.symbol, received_at=args.received_at, max_levels=args.max_levels)
        except OSError:
            report = _report(args.symbol, reason="INPUT_READ_ERROR")
    else:
        if args.input is not None or args.received_at is not None:
            parser.error("--fetch records its own local times; do not supply input/received-at")
        report = fetch_orderbook(symbol=args.symbol, fetch=True, timeout_seconds=args.timeout_seconds, max_levels=args.max_levels)
    print(canonical_json(report))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
