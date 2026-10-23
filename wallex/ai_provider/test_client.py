"""Synthetic offline coverage. All HTTP is injected or mocked."""

from dataclasses import asdict, replace
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from .client import (
    BoxAPIClient, DEFAULT_MODEL, ENDPOINT, HttpRequest, HttpResponse,
    MAX_RESPONSE_BYTES, REQUEST_LIMIT_MESSAGE, UrllibTransport, _NoRedirect,
)
from .models import AdvisoryContext

KEY = "synthetic-key-never-live-12345"


def advice(**changes):
    return dict({"version": "1", "decision": "REVIEW", "reason_codes": ["REVIEW_REQUIRED"],
                 "explanation": "A human should review the observed pullback."}, **changes)


def envelope(content=None, **changes):
    return dict({"model": "gpt-5.4-mini-snapshot", "choices": [{"index": 0,
                 "finish_reason": "stop", "message": {"role": "assistant",
                 "content": json.dumps(advice()) if content is None else content}}]}, **changes)


def reply(value=None, status=200):
    return HttpResponse(status, json.dumps(envelope() if value is None else value).encode())


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.post.return_value = reply()
        self.client = BoxAPIClient(api_key=KEY, transport=self.transport)
        self.context = AdvisoryContext("BTCUSDT", 65000, "UP", True,
                                       "VERIFIED", "VERIFIED", "VERIFIED")

    def assert_closed(self, result, status):
        self.assertEqual((result.decision, result.status, result.reason_codes),
                         ("NO_TRADE", status, (status,)))
        self.assertFalse(result.executable)
        self.assertNotIn(KEY, repr(result))
        self.assertNotIn(KEY, json.dumps(asdict(result)))

    def test_successful_advice_and_bounded_wire_contract(self):
        self.transport.post.assert_not_called()
        result = self.client.advise(self.context)
        self.assertEqual((result.decision, result.status, result.version), ("REVIEW", "OK", "1"))
        self.assertFalse(result.executable)
        self.assertEqual(result.requested_model, DEFAULT_MODEL)
        self.assertEqual(result.response_model, "gpt-5.4-mini-snapshot")
        self.assertEqual(result.provider, "BoxAPI")
        self.assertIsNone(result.usage)
        request = self.transport.post.call_args.args[0]
        self.assertEqual(request.url, ENDPOINT)
        self.assertEqual(request.timeout_seconds, 5)
        self.assertEqual(request.max_response_bytes, MAX_RESPONSE_BYTES)
        self.assertEqual(request.headers["Authorization"], "Bearer " + KEY)
        payload = json.loads(request.body)
        self.assertEqual(json.loads(payload["messages"][1]["content"]), asdict(self.context))
        self.assertEqual(payload["max_tokens"], 512)
        self.assertNotIn(KEY, request.body.decode())
        self.assertNotIn(KEY, repr(request))
        self.assertNotIn(KEY, repr(self.client))
        self.transport.post.assert_called_once()

    def test_no_trade_and_usage_preserve_only_reported_values(self):
        cases = [(None, None), ({"total_tokens": 14}, (None, None, 14)),
                 ({"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14,
                   "completion_tokens_details": {"reasoning_tokens": 2}}, (8, 6, 14))]
        for usage, expected in cases:
            with self.subTest(usage=usage):
                self.transport.post.return_value = reply(envelope(json.dumps(advice(
                    decision="NO_TRADE", reason_codes=["NO_SIGNAL"], explanation="No signal observed.")),
                    usage=usage, model=None))
                result = self.client.advise(self.context)
                self.assertEqual(result.decision, "NO_TRADE")
                self.assertEqual(result.status, "OK")
                self.assertIsNone(result.response_model)
                self.assertEqual(None if result.usage is None else tuple(asdict(result.usage).values()), expected)
                self.assertNotIn("cost", asdict(result))

    def test_every_blocked_or_unverified_gate_skips_transport(self):
        for field in ("data_quality", "risk", "credit"):
            for state in ("BLOCKED", "UNVERIFIED"):
                with self.subTest(field=field, state=state):
                    self.assert_closed(self.client.advise(replace(self.context, **{field: state})),
                                       "DETERMINISTIC_GATE_CLOSED")
        self.assert_closed(self.client.advise(AdvisoryContext("BTCUSDT", 65000, "UP", True)),
                           "DETERMINISTIC_GATE_CLOSED")
        self.transport.post.assert_not_called()

    def test_invalid_and_unsanitized_context_skips_transport(self):
        cases = [None, {}, "secret", replace(self.context, version="2")]
        fields = {"symbol": ["BTC\nUSDT", "A" * 25, "api_key", KEY, [], ""],
                  "last_price": [True, 0, -1, float("nan"), float("inf"), 1e19, "65000"],
                  "h1_trend": ["BUY", [], None], "m15_pullback": [1, None],
                  "risk": [True, [], "APPROVED"], "credit": [None], "data_quality": ["OK"]}
        for name, values in fields.items():
            cases.extend(replace(self.context, **{name: value}) for value in values)
        for context in cases:
            with self.subTest(context=context):
                self.assert_closed(self.client.advise(context), "INVALID_CONTEXT")
        self.transport.post.assert_not_called()

    def test_invalid_configuration_errors_are_sanitized(self):
        cases = [{"api_key": value} for value in (None, "", KEY + "\n", "x" * 513)]
        cases += [{"model": value} for value in (KEY, "Bearer secret", [], "m" * 97)]
        cases += [{"timeout_seconds": value} for value in (0, 31, True, "5", float("nan"), float("inf"))]
        cases += [{"transport": None}]
        for changes in cases:
            with self.subTest(fields=tuple(changes)):
                config = dict(api_key=KEY, transport=self.transport)
                config.update(changes)
                with self.assertRaises(ValueError) as caught:
                    BoxAPIClient(**config)
                self.assertEqual(str(caught.exception), "Invalid advisory client configuration")

    def test_transport_exceptions_are_closed_without_retries_or_echo(self):
        for exception, status in ((TimeoutError(KEY), "TIMEOUT"), (OSError(KEY), "TRANSPORT_ERROR"),
                                  (ValueError("Authorization: Bearer " + KEY), "TRANSPORT_ERROR")):
            with self.subTest(status=status):
                self.transport.reset_mock()
                self.transport.post.side_effect = exception
                self.assert_closed(self.client.advise(self.context), status)
                self.transport.post.assert_called_once()

    def test_http_status_and_exact_request_limit_classification(self):
        cases = [(401, KEY, "AUTHENTICATION_FAILED"), (403, "Forbidden", "FORBIDDEN"),
                 (429, "quota", "RATE_LIMITED"), (500, KEY, "HTTP_ERROR"),
                 (302, "redirect", "HTTP_ERROR"), (204, "", "HTTP_ERROR"),
                 (403, REQUEST_LIMIT_MESSAGE, "REQUEST_LIMIT_REACHED"),
                 (403, json.dumps({"error": {"message": REQUEST_LIMIT_MESSAGE}}), "REQUEST_LIMIT_REACHED"),
                 (403, json.dumps({"message": REQUEST_LIMIT_MESSAGE}), "REQUEST_LIMIT_REACHED"),
                 (403, json.dumps({"error": REQUEST_LIMIT_MESSAGE}), "REQUEST_LIMIT_REACHED"),
                 (403, "prefix " + REQUEST_LIMIT_MESSAGE, "FORBIDDEN"),
                 (403, json.dumps({"message": "Other denial", "detail": REQUEST_LIMIT_MESSAGE}), "FORBIDDEN")]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected):
                self.transport.post.return_value = HttpResponse(status, body.encode())
                self.assert_closed(self.client.advise(self.context), expected)

    def test_bad_transport_response_and_body_bounds(self):
        cases = [None, {}, HttpResponse(True, b""), HttpResponse(600, b""), HttpResponse(99, b""),
                 HttpResponse(200, "text"), HttpResponse(200, b"x" * (MAX_RESPONSE_BYTES + 1)),
                 HttpResponse(200, b"\xff"), HttpResponse(200, b"[]"), HttpResponse(200, b"")]
        for response in cases:
            with self.subTest(response_type=type(response)):
                self.transport.post.return_value = response
                self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")

    def test_strict_advisory_json_and_contradictions(self):
        cases = ["{}", "[]", "null", "```json\n{}\n```", "{} trailing", "[NaN]",
                 json.dumps(advice())[:-1] + ', "decision": "NO_TRADE"}',
                 json.dumps({k: v for k, v in advice().items() if k != "explanation"}),
                 json.dumps(advice(order={"side": "BUY"})), json.dumps(advice(version=1))]
        bad_fields = {"decision": ["BUY", "SELL", "APPROVE", None, []],
                      "reason_codes": [[], ["NO_SIGNAL"], ["REVIEW_REQUIRED"] * 2, [None],
                                       ["UNKNOWN"], "REVIEW_REQUIRED", ["REVIEW_REQUIRED", "NO_SIGNAL"]],
                      "explanation": ["", " ", "x" * 801, "BUY now", "sell now", "line\nbreak", None,
                                      "Trading is approved", "Execute the trade", "Submit orders"]}
        for field, values in bad_fields.items():
            cases.extend(json.dumps(advice(**{field: value})) for value in values)
        for content in cases:
            with self.subTest(content=content[:80]):
                self.transport.post.return_value = reply(envelope(content))
                self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")

    def test_truncation_refusal_tools_and_envelope_ambiguity(self):
        base = envelope()
        cases = [envelope(choices=[]), envelope(choices=base["choices"] * 2), envelope(error="denied"),
                 envelope(model=[]), envelope(model="m" * 97), envelope(choices=[None])]
        for finish in (None, "length", "tool_calls", "content_filter"):
            value = envelope()
            value["choices"][0]["finish_reason"] = finish
            cases.append(value)
        for field, item in (("role", "user"), ("refusal", "denied"), ("tool_calls", [{"id": "1"}]),
                            ("function_call", {}), ("content", []), ("content", "x" * 4097),
                            ("unexpected", True)):
            value = envelope()
            value["choices"][0]["message"][field] = item
            cases.append(value)
        value = envelope()
        value["choices"][0]["index"] = True
        cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                self.transport.post.return_value = reply(value)
                self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")
        for body in (b'{"choices": [], "choices": []}', b'{"choices": NaN}',
                     json.dumps(envelope(created=1)).replace('"created": 1', '"created": 1e999').encode()):
            self.transport.post.return_value = HttpResponse(200, body)
            self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")

    def test_invalid_or_contradictory_usage_is_not_invented(self):
        cases = [[], {"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": "14"},
                 {"prompt_tokens": 10, "total_tokens": 5},
                 {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 15},
                 {"total_tokens": 1e99}]
        for usage in cases:
            with self.subTest(usage=usage):
                self.transport.post.return_value = reply(envelope(usage=usage))
                self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")

    def test_provider_cannot_echo_credentials_in_results_or_logs(self):
        cases = [envelope(json.dumps(advice(explanation=KEY))), envelope(model=KEY),
                 envelope(id=KEY), envelope(usage={"detail": KEY}),
                 envelope(json.dumps(advice(explanation="Authorization: Bearer other-secret"))),
                 envelope(json.dumps(advice(explanation="api_key=other-secret")))]
        escaped = json.dumps(advice(explanation=KEY)).replace("synthetic", "\\u0073ynthetic")
        cases.append(envelope(escaped))
        with patch("sys.stdout", new_callable=io.StringIO) as stdout, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            for value in cases:
                self.transport.post.return_value = reply(value)
                self.assert_closed(self.client.advise(self.context), "INVALID_RESPONSE")
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.request = HttpRequest({"Authorization": "Bearer " + KEY,
                                    "Content-Type": "application/json"}, b"{}", 5)

    @patch(__package__ + ".client.build_opener")
    def test_https_post_is_explicit_bounded_and_does_not_follow_redirects(self, build):
        adapter = UrllibTransport()
        build.assert_not_called()
        response = build.return_value.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"{}"
        self.assertEqual(adapter.post(self.request), HttpResponse(200, b"{}"))
        wire = build.return_value.open.call_args.args[0]
        self.assertEqual(wire.full_url, ENDPOINT)
        self.assertEqual(wire.get_method(), "POST")
        self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 5})
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    @patch(__package__ + ".client.build_opener")
    def test_http_error_body_is_bounded_and_timeout_is_classified(self, build):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                body = io.BytesIO(REQUEST_LIMIT_MESSAGE.encode())
                build.return_value.open.side_effect = HTTPError(ENDPOINT, status, "error", {}, body)
                result = UrllibTransport().post(self.request)
                self.assertEqual(result.status, status)
                self.assertEqual(result.body, REQUEST_LIMIT_MESSAGE.encode())
                self.assertTrue(body.closed)
        for reason, exception in ((TimeoutError(KEY), TimeoutError), (OSError(KEY), OSError)):
            build.return_value.open.side_effect = URLError(reason)
            with self.assertRaises(exception) as caught:
                UrllibTransport().post(self.request)
            self.assertNotIn(KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
