"""Package-independent scoped GET checks using synthetic account evidence only."""

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from . import spot_preflight as spot
from .credit_readonly import HttpGet, HttpReply
from .test_credit_readonly import FAKE, setup_fixture


def fixture():
    binding, _, payloads, _ = setup_fixture()
    cfg = spot.SpotAccountConfig(binding.sub_account_client_id, binding.owner_user_id, "BTCTMN", "BTC", "TMN", True)
    accounts = deepcopy(payloads[0])
    balances = {"success": True, "result": {"TMN": {"symbol": "TMN", "total": "100", "freeze": "0", "available": "100"},
                                         "BTC": {"symbol": "BTC", "total": "0", "freeze": "0", "available": "0"}}}
    orders = {"success": True, "result": {"orders": []}, "result_info": {"page": 1, "count": 0, "total_count": 0}}
    market = {"success": True, "result": {"markets": [{"symbol": "BTCTMN", "base_asset": "BTC", "quote_asset": "TMN",
                                                       "is_spot": True, "amount_precision": 6, "price_precision": 0}]}}
    return cfg, accounts, balances, orders, market


def invoke(case=None):
    cfg, accounts, balances, orders, market = fixture()
    if case == "wrong-owner": accounts["result"][0]["userId"] = "99999999"
    if case == "main": accounts["result"][0]["type"] = "main"
    if case == "zero": balances["result"]["TMN"].update(total="0", available="0")
    if case == "mismatch": balances["result"]["TMN"]["available"] = "101"
    if case == "missing": del balances["result"]["BTC"]
    if case == "metadata": orders["result_info"] = None
    if case == "market": market["result"]["markets"][0]["quote_asset"] = "USDT"
    if case == "exposure": balances["result"]["BTC"].update(total="0.001", available="0.001")
    if case == "orders":
        orders["result"]["orders"] = [{"clientOrderId": "synthetic-order-not-a-real-id", "symbol": "BTCTMN", "active": True,
                                        "status": "NEW", "side": "BUY", "origQty": "0.01", "executedQty": "0"}]
        orders["result_info"].update(count=1,total_count=1)
    paths=[]
    def get(request):
        paths.append(request.path)
        if request.path == "/sub-accounts": payload = accounts
        elif request.path == "/v1/account/balances-detail": payload = balances
        elif request.path.startswith("/v1/account/openOrders?"):
            payload = orders if "page=1&" in request.path else {"success":True, "result":{"orders":[]}}
        else: raise AssertionError("Forbidden endpoint")
        if case in ("401", "403", "500") and request.path == "/v1/account/balances-detail": return HttpReply(int(case),b"secret-body")
        return HttpReply(200,json.dumps(payload).encode())
    transport=Mock()
    transport.get.side_effect=get
    report=spot.check_account(cfg,connect=True,transport=transport,resolve_credential=lambda ref:FAKE,
                              market_get=lambda:market,clock=lambda:1800000)
    return report,paths


class SpotPreflightTests(unittest.TestCase):
    def test_successful_reads_do_not_require_packages_or_authorize_trading(self):
        report,paths=invoke()
        self.assertTrue(report["authenticated_reads_succeeded"])
        self.assertEqual(paths,["/sub-accounts","/v1/account/balances-detail","/v1/account/openOrders?page=1&per_page=100"])
        self.assertTrue(report["quote_available_positive"])
        self.assertFalse(report["base_exposure_present"])
        self.assertEqual(report["order_coverage"],"UNVERIFIED_PROVIDER_PAGINATION")
        self.assertEqual(report["open_orders_observed"],0)
        self.assertIn("ACTUAL_FEES_UNVERIFIED",report["blockers"])
        self.assertIn("TRADE_PERMISSION_NOT_VERIFIED_BY_READS",report["blockers"])
        self.assertEqual(report["action"],"NO_TRADE")
        self.assertFalse(report["executable_ready"])
        self.assertNotIn(FAKE,spot.canonical_json(report))
        self.assertNotIn("synthetic-credit-001",spot.canonical_json(report))
        cfg, accounts, balances, orders, market = fixture()
        transport = Mock()
        transport.get.side_effect = [HttpReply(200, json.dumps(p).encode()) for p in (accounts, accounts, balances, orders)]
        selection = Mock(side_effect=lambda options: options[0])
        result = spot.check_account(connect=True, transport=transport, resolve_credential=lambda ref: FAKE,
                                    select_account=selection, market_get=lambda: market, clock=lambda: 1800000)
        self.assertTrue(result["authenticated_reads_succeeded"])
        self.assertEqual(selection.call_args.args[0][0], cfg)
        requests = [call.args[0] for call in transport.get.call_args_list]
        self.assertNotIn("sub-account-client-id", requests[0].headers)
        self.assertTrue(all(r.headers["sub-account-client-id"] == cfg.sub_account_client_id for r in requests[1:]))
        for case in ("cancel", "duplicate", "null-owner", "changed-after-confirmation"):
            with self.subTest(interactive=case):
                payload = deepcopy(accounts)
                if case == "duplicate":
                    payload["result"] *= 2
                    payload["result_info"].update(count=2, total_count=2)
                if case == "null-owner": payload["result"][0]["userId"] = None
                changed = deepcopy(accounts)
                changed["result"][0]["userId"] = "99999999"
                transport = Mock()
                transport.get.side_effect = [HttpReply(200, json.dumps(p).encode()) for p in (payload, changed)]
                result = spot.check_account(connect=True, transport=transport, resolve_credential=lambda ref: FAKE,
                                            select_account=lambda options: None if case == "cancel" else options[0],
                                            market_get=lambda: market, clock=lambda: 1800000)
                self.assertFalse(result["authenticated_reads_succeeded"])
                self.assertFalse(result["executable_ready"])
                self.assertLessEqual(transport.get.call_count, 2)


    def test_identity_balance_exposure_pagination_http_and_market_uncertainty_fail_closed(self):
        for case in ("wrong-owner","main","zero","mismatch","missing","metadata","market","exposure","orders","401","403","500"):
            with self.subTest(case=case):
                report,paths=invoke(case)
                self.assertFalse(report["executable_ready"])
                self.assertEqual(report["action"],"NO_TRADE")
                self.assertTrue(report["blockers"])
                text=spot.canonical_json(report)
                self.assertNotIn(FAKE,text)
                self.assertNotIn("secret-body",text)
                self.assertNotIn("synthetic-order-not-a-real-id",text)
                if case in ("wrong-owner","main"):
                    self.assertEqual(paths,["/sub-accounts"])
                    self.assertFalse(report["authenticated_reads_succeeded"])
                if case in ("401", "403", "500"):
                    self.assertFalse(report["authenticated_reads_succeeded"])
                    self.assertIn("balances:SCOPED_HTTP_"+case,report["blockers"])
                    self.assertTrue(any("openOrders" in path for path in paths))
                if case=="metadata": self.assertIn("ORDER_COVERAGE_UNVERIFIED",report["blockers"])
                if case=="orders": self.assertIn("OPEN_OR_UNKNOWN_ORDERS_PRESENT",report["blockers"])
                if case=="exposure": self.assertIn("EXPOSURE_RECONCILIATION_REQUIRED",report["blockers"])

    def test_untyped_spot_metadata_does_not_imply_auth_failure_or_complete_coverage(self):
        cfg, accounts, balances, orders, market = fixture()
        for info in (None, {}, "synthetic-private-metadata-not-to-echo", [],
                     {"page":"1", "count":"0", "total_count":"0"},
                     {"page":1, "count":100, "total_count":0},
                     {"page":1, "count":0, "total_count":0}):
            with self.subTest(format=type(info).__name__):
                transport = Mock()
                response = deepcopy(orders)
                response["result_info"] = info
                transport.get.side_effect = [HttpReply(200, json.dumps(p).encode()) for p in (accounts, balances, response)]
                report = spot.check_account(cfg, connect=True, transport=transport, resolve_credential=lambda ref:FAKE,
                                            market_get=lambda:market, clock=lambda:1800000)
                self.assertTrue(report["authenticated_reads_succeeded"])
                self.assertEqual(report["open_orders_observed"],0)
                self.assertEqual(report["order_coverage"],"UNVERIFIED_PROVIDER_PAGINATION")
                self.assertIn("ORDER_COVERAGE_UNVERIFIED",report["blockers"])
                self.assertEqual(report["order_pagination_diagnostics"][0]["result_info_format"],spot._format(info))
                self.assertNotIn("synthetic-private-metadata-not-to-echo",spot.canonical_json(report))
                self.assertEqual(report["action"],"NO_TRADE")
                self.assertFalse(report["executable_ready"])
                self.assertEqual(transport.get.call_count,3)
        # Never continue beyond the fixed three-page budget, and reject ignored
        # page parameters/duplicate order identities instead of double counting.
        for duplicate in (False,True):
            with self.subTest(duplicate=duplicate):
                pages=[]
                for i in range(3):
                    pages.append({"success":True,"result":{"orders":[{
                        "clientOrderId": "synthetic-"+str(0 if duplicate else i),
                        "symbol":"BTCTMN","active":True,"side":"BUY","status":"NEW",
                        "origQty":"0.1","executedQty":"0"}]}})
                transport=Mock()
                transport.get.side_effect=[HttpReply(200,json.dumps(p).encode()) for p in (accounts,balances,*pages)]
                report=spot.check_account(cfg,connect=True,transport=transport,resolve_credential=lambda ref:FAKE,
                                          market_get=lambda:market,clock=lambda:1800000)
                self.assertLessEqual(transport.get.call_count,5)
                self.assertFalse(report["executable_ready"])
                if duplicate:
                    self.assertFalse(report["authenticated_reads_succeeded"])
                    self.assertIn("orders:MALFORMED_DUPLICATE_OR_UNKNOWN_ORDER_EVIDENCE",report["blockers"])
                else:
                    self.assertTrue(report["authenticated_reads_succeeded"])
                    self.assertIn("ORDER_PAGINATION_BUDGET_EXHAUSTED",report["blockers"])

    def test_optin_hidden_prompt_and_get_route_boundaries(self):
        cfg,*_=fixture()
        resolver,transport=Mock(),Mock()
        report=spot.check_account(cfg,transport=transport,resolve_credential=resolver)
        resolver.assert_not_called()
        transport.get.assert_not_called()
        self.assertIn("EXPLICIT_READ_CONNECT_REQUIRED",report["blockers"])
        transport=spot.SpotGetTransport()
        for path in ("/v1/prop/user/packages/active","/v1/account/balances","/v1/account/orders","/v1/account/openOrders?page=4&per_page=100"):
            with self.subTest(path=path),self.assertRaises(ValueError):
                transport.get(HttpGet(path,{"x-api-key":FAKE,"sub-account-client-id":cfg.sub_account_client_id},5,1024))
        # Exercise the actual transport validation, not just injected get().
        response = Mock(status=200)
        response.read.return_value = b'{"success":true}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch("wallex.infra.credit_readonly.build_opener", return_value=opener):
            reply = transport.get(HttpGet("/sub-accounts", {"x-api-key": FAKE}, 5, 1024))
        self.assertEqual(reply.status, 200)
        wire = opener.open.call_args.args[0]
        self.assertEqual(wire.get_method(), "GET")
        self.assertEqual(wire.full_url, "https://api.wallex.ir/sub-accounts")
        self.assertNotIn("Sub-account-client-id", dict(wire.header_items()))
        for path in ("/v1/account/balances-detail", "/v1/account/openOrders?page=1&per_page=100"):
            with self.subTest(missing_scope_header=path), self.assertRaises(ValueError):
                transport.get(HttpGet(path, {"x-api-key": FAKE}, 5, 1024))
        args=["--connect","--symbol","BTCTMN","--base","BTC","--quote","TMN"]
        out=StringIO()
        from types import SimpleNamespace
        settings=SimpleNamespace(wallex_api_key=FAKE,wallex_subaccount_id=cfg.sub_account_client_id,
            wallex_symbol="BTCTMN",assets=("BTC","TMN"))
        with patch("wallex.infra.config.load_settings",return_value=settings), \
             patch.object(spot,"_prompt_credential") as prompt, \
             patch.object(spot,"check_account",return_value=spot._report()) as check,redirect_stdout(out):
            self.assertEqual(spot.main(args),1)
        self.assertEqual(check.call_args.kwargs["resolve_credential"]("ignored"),FAKE)
        prompt.assert_not_called()
        self.assertTrue(check.call_args.kwargs["connect"])
        self.assertEqual(check.call_args.kwargs["select_account"]((cfg,)),cfg)
        self.assertFalse(json.loads(out.getvalue())["executable_ready"])
