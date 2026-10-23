"""Mock transport only. Credentials and all account evidence are synthetic."""

from dataclasses import replace
import json
from pathlib import Path
import ssl
import unittest
from unittest.mock import Mock, patch

from wallex.strategy.engine import Signal
from . import credit_account as account, credit_readonly as reader

FAKE = "mock-only-not-a-valid-api-key"
R = reader.ReadReason


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def get(self, request):
        self.calls.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def setup_fixture():
    raw = json.loads((Path(__file__).parent / 'fixtures' /
                      'credit_account_synthetic.json').read_text(encoding='utf-8'))
    binding = dict(raw['binding'])
    binding['kind'] = account.AccountKind.TRADE_CREDIT
    binding = account.CreditAccountBinding(**binding)
    data = dict(raw['semantics'])
    data['balance_units'] = tuple(tuple(p) for p in data['balance_units'])
    semantics = account.CreditSemantics(**data)
    payloads = [raw[name]['payload'] for name in ('accounts','plan','balances')]
    config = reader.ReadOnlyConfig(
        'synthetic-resolver-reference', credential_sub_account_client_id=binding.sub_account_client_id,
        scope=reader.CredentialScope.TRADE_CREDIT, read_permission_confirmed=True, opt_in=True,
        timeout_seconds=3, max_accounts=5, max_response_bytes=65536,
    )
    return binding, semantics, payloads, config


def mock_client(payloads, *, replies=None, clock_values=(1799990,1799995,1799998)):
    replies = replies if replies is not None else [reader.HttpReply(200,json.dumps(p).encode()) for p in payloads]
    transport = FakeTransport(replies)
    resolver = Mock(return_value=FAKE)
    clock = Mock(side_effect=clock_values)
    client = reader.CreditReadOnlyClient(transport=transport,resolve_credential=resolver,
                                       observation_clock=clock)
    return client,transport,resolver,clock


class CreditReadOnlyTests(unittest.TestCase):
    def assert_failed(self, result, reason):
        self.assertFalse(result.collected)
        self.assertEqual(result.reason,reason)
        self.assertIsNone(result.bundle)
        self.assertFalse(result.credential_scope_verified or result.executable_ready)
        self.assertIs(result.executable_action,Signal.NO_TRADE)
        self.assertTrue(FAKE not in repr(result))

    def test_disabled_or_unverified_scope_never_resolves_or_sends_credentials(self):
        binding,_,payloads,config = setup_fixture()
        cases = (
            (reader.ReadOnlyConfig('synthetic-reference'),R.DISABLED),
            (replace(config,scope=reader.CredentialScope.UNVERIFIED),R.UNVERIFIED_SCOPE),
            (replace(config,scope='MAIN'),R.UNVERIFIED_SCOPE),
            (replace(config,read_permission_confirmed=False),R.UNVERIFIED_SCOPE),
            (replace(config,credential_sub_account_client_id=None),R.UNVERIFIED_SCOPE),
            (replace(config,credential_sub_account_client_id='other-credit'),R.UNVERIFIED_SCOPE),
        )
        for cfg,reason in cases:
            with self.subTest(reason=reason):
                client,transport,resolver,_ = mock_client(payloads)
                self.assert_failed(client.fetch(binding,config=cfg),reason)
                resolver.assert_not_called()
                self.assertEqual(len(transport.calls),0)

    def test_three_scoped_gets_preserve_times_and_feed_existing_offline_adapter(self):
        binding,semantics,payloads,config = setup_fixture()
        client,transport,resolver,_ = mock_client(payloads)
        result = client.fetch(binding,config=config)
        self.assertTrue(result.collected)
        self.assertEqual([q.path for q in transport.calls],list(reader.GET_PATHS))
        self.assertEqual(resolver.call_count,1)
        for request in transport.calls:
            self.assertEqual(request.method,'GET')
            self.assertEqual(request.timeout_seconds,3)
            self.assertEqual(set(request.headers),{'x-api-key','sub-account-client-id'})
            self.assertTrue(request.headers['x-api-key'] == FAKE)
            self.assertEqual(request.headers['sub-account-client-id'],binding.sub_account_client_id)
            self.assertTrue(FAKE not in repr(request))
        evidence = result.bundle.for_assessment(semantics=semantics,as_of=1800000,max_age_seconds=30)
        assessment = account.assess_credit_account(evidence)
        self.assertTrue(assessment.snapshot_valid,assessment)
        self.assertEqual(assessment.snapshot.observed_times,
                         (('accounts',1799990),('plan',1799995),('balances',1799998)))
        self.assertIsNone(assessment.snapshot.risk_equity)
        self.assertFalse(result.credential_scope_verified or result.executable_ready)
        self.assertIs(result.executable_action,Signal.NO_TRADE)
        self.assertTrue(assessment.blocks_new_entries)
        self.assertTrue(FAKE not in repr(result))
        # Handoff returns fresh payloads, not shared mutable client state.
        evidence.balances.payload['result'].clear()
        fresh = result.bundle.for_assessment(semantics=semantics,as_of=1800000,max_age_seconds=30)
        self.assertTrue(account.assess_credit_account(fresh).snapshot_valid)

    def test_explicit_binding_and_discovered_identity_are_never_guessed(self):
        binding,_,payloads,config = setup_fixture()
        for changes in ({'kind':'MAIN'},{'sub_account_client_id':''},{'package_id':0},{'user_package_id':True}):
            with self.subTest(binding=changes):
                client,transport,resolver,_ = mock_client(payloads)
                self.assert_failed(client.fetch(replace(binding,**changes),config=config),R.INVALID_BINDING)
                resolver.assert_not_called()
                self.assertEqual(len(transport.calls),0)
        for target in ('owner','account','package'):
            _,_,payloads,_ = setup_fixture()
            if target == 'owner':payloads[0]['result'][0]['userId'] = 99
            elif target == 'account':payloads[0]['result'][0]['clientId'] = 'main-account'
            else:payloads[1]['result']['packageDetail']['id'] = 99
            with self.subTest(target=target):
                client,transport,_,_ = mock_client(payloads)
                self.assert_failed(client.fetch(binding,config=config),R.AMBIGUOUS_SCOPE)
                self.assertLessEqual(len(transport.calls),2)

    def test_http_failures_have_explicit_codes_and_no_retries(self):
        binding,_,payloads,config = setup_fixture()
        for status,reason in ((401,R.AUTHENTICATION_FAILED),(403,R.FORBIDDEN),(404,R.NOT_FOUND),
                              (429,R.RATE_LIMITED),(500,R.SERVER_ERROR),(503,R.SERVER_ERROR),(302,R.HTTP_ERROR)):
            with self.subTest(status=status):
                client,transport,_,_ = mock_client(payloads,replies=[reader.HttpReply(status,FAKE.encode())])
                result = client.fetch(binding,config=config)
                self.assert_failed(result,reason)
                self.assertEqual(result.http_status,status)
                self.assertEqual(len(transport.calls),1)

    def test_late_failure_discards_partial_evidence_without_main_account_fallback(self):
        binding,_,payloads,config = setup_fixture()
        replies = [reader.HttpReply(200,json.dumps(payloads[0]).encode()),reader.HttpReply(404,b'')]
        client,transport,_,_ = mock_client(payloads,replies=replies)
        self.assert_failed(client.fetch(binding,config=config),R.NOT_FOUND)
        self.assertEqual([q.path for q in transport.calls],list(reader.GET_PATHS[:2]))
        self.assertTrue(all(q.headers['sub-account-client-id'] == binding.sub_account_client_id
                            for q in transport.calls))

    def test_timeout_transport_and_resolver_errors_never_expose_details(self):
        binding,_,payloads,config = setup_fixture()
        for error,reason in ((TimeoutError(FAKE),R.TIMEOUT),(RuntimeError(FAKE),R.TRANSPORT_ERROR)):
            with self.subTest(reason=reason):
                client,transport,_,_ = mock_client(payloads,replies=[error])
                self.assert_failed(client.fetch(binding,config=config),reason)
                self.assertEqual(len(transport.calls),1)
        client,transport,resolver,_ = mock_client(payloads)
        resolver.side_effect = RuntimeError(FAKE)
        self.assert_failed(client.fetch(binding,config=config),R.CREDENTIAL_UNAVAILABLE)
        self.assertEqual(len(transport.calls),0)

    def test_timeout_body_and_row_resource_limits_are_enforced(self):
        binding,_,payloads,config = setup_fixture()
        for changes in ({'timeout_seconds':0},{'timeout_seconds':True},{'timeout_seconds':61},
                        {'max_accounts':0},{'max_response_bytes':0},{'max_response_bytes':1048577}):
            with self.subTest(changes=changes):
                client,transport,resolver,_ = mock_client(payloads)
                self.assert_failed(client.fetch(binding,config=replace(config,**changes)),R.INVALID_CONFIG)
                resolver.assert_not_called()
                self.assertEqual(len(transport.calls),0)
        client,transport,_,_ = mock_client(payloads,replies=[reader.HttpReply(200,b' '*101)])
        self.assert_failed(client.fetch(binding,config=replace(config,max_response_bytes=100)),R.RESPONSE_TOO_LARGE)
        self.assertEqual(len(transport.calls),1)

    def test_discovery_pagination_is_bounded_and_partial_or_duplicate_pages_reject(self):
        binding,_,_,config = setup_fixture()
        for change in ('partial','wrong-page','duplicate','over-budget'):
            _,_,payloads,_ = setup_fixture()
            page = payloads[0]
            if change == 'partial':page['result_info']['total_count'] = 2
            elif change == 'wrong-page':page['result_info']['page'] = 2
            else:
                page['result'] *= 2
                page['result_info'].update(count=2,total_count=2)
                if change == 'over-budget':page['result'][1] = dict(page['result'][1],clientId='another-credit')
            with self.subTest(change=change):
                client,transport,_,_ = mock_client(payloads)
                cfg = replace(config,max_accounts=1) if change == 'over-budget' else config
                result = client.fetch(binding,config=cfg)
                self.assertFalse(result.collected)
                self.assertIsNone(result.bundle)
                self.assertEqual(len(transport.calls),1)
                self.assertEqual(transport.calls[0].path,'/sub-accounts')

    def test_malformed_unsuccessful_or_ambiguous_json_fails_closed(self):
        binding,_,payloads,config = setup_fixture()
        for body in (b'not-json',b'{"success":true,"success":false}',b'{"success":NaN}',
                     b'[]',b'{"success":false,"result":{}}',b'\xff'):
            with self.subTest(body_kind=len(body)):
                client,_,_,_ = mock_client(payloads,replies=[reader.HttpReply(200,body)])
                self.assert_failed(client.fetch(binding,config=config),R.INVALID_RESPONSE)

    def test_sanitized_wire_evidence_omits_headers_messages_and_reflected_secret(self):
        binding,_,payloads,config = setup_fixture()
        for payload in payloads:
            payload['message'] = FAKE
            payload['headers'] = {'x-api-key':FAKE}
        client,_,_,_ = mock_client(payloads)
        result = client.fetch(binding,config=config)
        self.assertTrue(result.collected)
        for read in (result.bundle.accounts,result.bundle.plan,result.bundle.balances):
            self.assertTrue(FAKE not in read.payload_json)
            self.assertNotIn('headers',json.loads(read.payload_json))
            self.assertNotIn('message',json.loads(read.payload_json))
        payloads[1]['result']['userPackageDetail']['status'] = FAKE
        client,_,_,_ = mock_client(payloads)
        self.assert_failed(client.fetch(binding,config=config),R.SENSITIVE_RESPONSE)

    def test_observation_clock_must_be_explicit_valid_and_non_decreasing(self):
        binding,_,payloads,config = setup_fixture()
        for times in ((None,), (True,), (-1,), (1799990,1799989)):
            with self.subTest(times=times):
                client,_,_,_ = mock_client(payloads,clock_values=times)
                self.assert_failed(client.fetch(binding,config=config),R.INVALID_OBSERVATION_TIME)

    def test_optional_urllib_transport_is_fixed_get_tls_bounded_and_no_redirect(self):
        binding,_,_,config = setup_fixture()
        request = reader.HttpGet('/sub-accounts',{'x-api-key':FAKE,
                    'sub-account-client-id':binding.sub_account_client_id},3,100)
        response = Mock(status=200)
        response.read.return_value = b'{}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = context
        with patch.object(reader,'build_opener',return_value=opener) as build:
            reply = reader.UrllibGetTransport().get(request)
        self.assertEqual(reply.status,200)
        response.read.assert_called_once_with(101)
        wire = opener.open.call_args.args[0]
        self.assertEqual(wire.full_url,'https://api.wallex.ir/sub-accounts')
        self.assertEqual(wire.get_method(),'GET')
        self.assertEqual(opener.open.call_args.kwargs,{'timeout':3})
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies,{})
        self.assertIsNone(handlers[1].redirect_request(None,None,302,'',{},'https://example.invalid'))
        self.assertTrue(handlers[2]._context.check_hostname)
        self.assertEqual(handlers[2]._context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(FAKE not in repr(reply))
        for bad_path in ('https://example.invalid','/v1/account/orders','/sub-accounts?page=2'):
            with self.subTest(path=bad_path),patch.object(reader,'build_opener') as build:
                with self.assertRaises(ValueError):reader.UrllibGetTransport().get(replace(request,path=bad_path))
                build.assert_not_called()
        with self.assertRaises((TypeError,ValueError)):replace(request,method='POST')


    def test_prop_diagnostic_distinguishes_auth_absence_and_malformed_evidence(self):
        binding, _, _, config = setup_fixture()
        cases = (("ok", 200, None, "PROP_PACKAGE_READABLE"),
                 ("401", 401, R.AUTHENTICATION_FAILED, "PROP_PACKAGE_AUTH_REJECTED"),
                 ("403", 403, R.FORBIDDEN, "PROP_PACKAGE_ACCESS_DENIED"),
                 ("404", 404, R.NOT_FOUND, "PROP_PACKAGE_HTTP_NOT_FOUND"),
                 ("null", 200, R.PACKAGE_EVIDENCE_ABSENT, "ACTIVE_PACKAGE_EVIDENCE_ABSENT"),
                 ("empty", 200, R.PACKAGE_EVIDENCE_ABSENT, "ACTIVE_PACKAGE_EVIDENCE_ABSENT"),
                 ("both-null", 200, R.PACKAGE_EVIDENCE_ABSENT, "ACTIVE_PACKAGE_EVIDENCE_ABSENT"),
                 ("malformed", 200, R.INVALID_RESPONSE, "INVALID_RESPONSE"),
                 ("reflected", 200, R.SENSITIVE_RESPONSE, "SENSITIVE_RESPONSE"))
        for case, status, reason, category in cases:
            _, _, payloads, _ = setup_fixture()
            if case == "null": payloads[1]["result"] = None
            elif case == "empty": payloads[1]["result"] = {}
            elif case == "both-null": payloads[1]["result"] = dict(packageDetail=None, userPackageDetail=None)
            elif case == "reflected": payloads[1]["result"]["userPackageDetail"]["status"] = FAKE
            body = b'not-json' if case == "malformed" else json.dumps(payloads[1]).encode()
            if status != 200: body = FAKE.encode()  # Provider error bodies must never be retained.
            replies = [reader.HttpReply(200, json.dumps(payloads[0]).encode()), reader.HttpReply(status, body)]
            with self.subTest(case=case):
                client, transport, _, _ = mock_client(payloads, replies=replies)
                result = client.fetch(binding, config=config, diagnose_prop=True)
                self.assert_failed(result, reason or R.PROP_DIAGNOSED)
                self.assertEqual(result.diagnostic_classification, category)
                self.assertEqual(result.http_status, status)
                self.assertEqual(result.scope_checks["account_type"], "prop")
                self.assertTrue(result.scope_checks["user_id_match"])
                self.assertEqual([request.path for request in transport.calls], list(reader.GET_PATHS[:2]))
                self.assertTrue(all(request.method == "GET" and request.headers["sub-account-client-id"] == binding.sub_account_client_id
                                    and request.headers["x-api-key"] == FAKE for request in transport.calls))
                if reason: self.assertEqual(result.failed_endpoint, reader.GET_PATHS[1])

    def test_prop_identity_mismatches_block_before_further_reads(self):
        binding, _, _, config = setup_fixture()
        cases = (("account", "ACCOUNT_IDENTITY_NOT_FOUND", 1), ("owner", "OWNER_IDENTITY_MISMATCH", 1),
                 ("type", "ACCOUNT_TYPE_MISMATCH", 1), ("duplicate", "AMBIGUOUS_DISCOVERY_IDENTITIES", 1),
                 ("inactive", "ACCOUNT_INACTIVE_OR_SUSPENDED", 1), ("suspended", "ACCOUNT_INACTIVE_OR_SUSPENDED", 1),
                 ("bad-status", "MALFORMED_ACCOUNT_STATUS", 1), ("package", "PACKAGE_IDENTITY_MISMATCH", 2),
                 ("user-package", "USER_PACKAGE_IDENTITY_MISMATCH", 2))
        for case, category, calls in cases:
            _, _, payloads, _ = setup_fixture()
            row = payloads[0]["result"][0]
            if case == "account": row["clientId"] = "wrong-synthetic-id"
            elif case == "owner": row["userId"] = 99
            elif case == "type": row["type"] = "spot"
            elif case == "inactive": row["isActive"] = False
            elif case == "suspended": row["isSuspend"] = True
            elif case == "bad-status": row["isActive"] = "true"
            elif case == "duplicate":
                payloads[0]["result"].append(dict(row))
                payloads[0]["result_info"].update(count=2, total_count=2)
            elif case == "package": payloads[1]["result"]["packageDetail"]["id"] = 99
            else: payloads[1]["result"]["userPackageDetail"]["id"] = 99
            with self.subTest(case=case):
                client, transport, _, _ = mock_client(payloads)
                result = client.fetch(binding, config=config, diagnose_prop=True)
                self.assertFalse(result.collected)
                self.assertIsNone(result.bundle)
                self.assertEqual(result.diagnostic_classification, category)
                self.assertEqual(result.http_status, 200)
                self.assertEqual(len(transport.calls), calls)
                self.assertIs(result.executable_action, Signal.NO_TRADE)
        client, transport, resolver, _ = mock_client(payloads)
        self.assert_failed(client.fetch(binding, config=config, diagnose_prop=True, diagnose_scope=True), R.INVALID_CONFIG)
        self.assertEqual(transport.calls, [])
        resolver.assert_not_called()

if __name__ == '__main__':
    unittest.main()
