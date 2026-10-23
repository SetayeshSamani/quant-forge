# BoxAPI advisory provider (contract v1)

A standard-library-only advisory provider. Importing or constructing it performs
no requests. There are no order methods, background calls or retries. The explicit
TP-1 research integration below journals structured evidence without activating
Risk, Credit or execution.

## Explicit integration contract

The caller supplies `AdvisoryContext`, a credential, and a trusted `Transport`.
Only explicit `client.advise(context)` or the separate technical research
`client.review_technical(context)` can invoke `transport.post(request)`. The
original advise() VERIFIED gates remain unchanged.
The transport receives sensitive headers and must honor timeout/response bounds
without logging, retaining, or exporting those headers. `UrllibTransport` is an
optional explicit implementation with TLS verification, a fixed HTTPS endpoint,
disabled redirects and ambient proxies, and no retry. Its timeout bounds socket
operations; callers needing a total elapsed deadline must enforce it in their
injected transport. Responses are limited to 65,536 bytes.

`AdvisoryContext` accepts exactly these dataclass fields:

| Field | Contract |
| --- | --- |
| `version` | `"1"` (default) |
| `symbol` | 2–24 uppercase ASCII letters/digits/underscore/hyphen, first character alphanumeric |
| `last_price` | Finite positive int/float, at most `1e18`; booleans rejected |
| `h1_trend` | `UP`, `DOWN`, `FLAT`, or `UNVERIFIED` |
| `m15_pullback` | Boolean |
| `data_quality`, `risk`, `credit` | `VERIFIED`, `BLOCKED`, or `UNVERIFIED` (default) |

There is no arbitrary prompt, account payload, user identifier, metadata mapping,
or free-text market context. The caller must supply a sanitized snapshot and
determine freshness, strategy semantics, and deterministic gate status separately.
`VERIFIED` is caller attestation, **not proof or authorization**. Any blocked or
unverified gate returns `NO_TRADE` without calling the provider. Invalid context
also skips the provider.

Provider content must be a JSON object with exactly:

```json
{"version":"1","decision":"REVIEW","reason_codes":["REVIEW_REQUIRED"],"explanation":"A human should review the observed pullback."}
```

| Decision | Allowed reason codes |
| --- | --- |
| `NO_TRADE` | `NO_SIGNAL`, `TREND_UNFAVORABLE`, `PULLBACK_ABSENT`, `INSUFFICIENT_EVIDENCE` |
| `REVIEW` | `REVIEW_REQUIRED`, `UNCERTAINTY` |

Codes must contain 1–4 unique entries belonging to the chosen decision.
Explanation is 1–800 characters, without surrounding whitespace, ASCII control
characters, credential markers, or order/authorization terms. This conservative
text filter can reject harmless prose. Prose is always untrusted; it must never
be parsed as an instruction. Mixing reason codes across decisions is rejected.
Duplicate JSON keys, nonfinite numbers, extra content fields, markdown wrappers,
multiple choices, truncation, refusal, and function/tool calls are rejected.
The outer completion envelope permits standard metadata fields only; a single
index-zero assistant message must finish with `stop`.

`AdvisoryResult` includes local version, decision, tuple of reason codes,
explanation, status, provider (`BoxAPI`), requested model, optional response model,
optional token usage, and immutable `executable=False`. `REVIEW` means human
review only. Neither decision permits any BUY/SELL, order submission, or bypass
of deterministic controls. Integration into an execution pipeline requires a
separate change by its owner; this package provides no execution authorization.

Reported prompt/completion/total token counts are preserved when present and
validated for nonnegative integer values and consistency. Missing model/usage
remains `None`; no token totals, costs, balances, or quota availability are
invented. Unknown usage detail fields are not exported.

## Failure behavior

Every failure returns `NO_TRADE`, a fixed explanation, `executable=False`, and a
local status also used as the single reason code:

- `INVALID_CONTEXT`, `DETERMINISTIC_GATE_CLOSED`
- `TIMEOUT`, `TRANSPORT_ERROR`
- `AUTHENTICATION_FAILED` (401), `FORBIDDEN` (other 403), `RATE_LIMITED` (429)
- `REQUEST_LIMIT_REACHED` (403 with the exact known request-limit message)
- `HTTP_ERROR` (other non-200 status), `INVALID_RESPONSE`

The known limit message is `Request limit reached. You have no more API requests
left.` It is recognized as a complete plain-text body, top-level JSON `message`,
JSON `error` string, or `error.message`, never by a loose substring match.
Oversized or invalid transport responses are rejected before classification.
Failed responses export no raw body, exception, response model, or usage.
Request headers/body and reply bodies are excluded from reprs. The client never
logs; known injected credential echoes and credential-bearing text are rejected,
including escaped JSON content. No owner credential is needed for tests.

## Offline usage and tests

```python
from wallex.ai_provider import AdvisoryContext, BoxAPIClient, HttpResponse

class OfflineTransport:
    def post(self, request):
        return HttpResponse(403, b"Request limit reached. You have no more API requests left.")

client = BoxAPIClient(api_key="synthetic-only-not-a-real-key", transport=OfflineTransport())
context = AdvisoryContext("BTCUSDT", 65000, "UP", True,
                          data_quality="VERIFIED", risk="VERIFIED", credit="VERIFIED")
result = client.advise(context)  # NO_TRADE / REQUEST_LIMIT_REACHED
assert result.executable is False
```

From the repository root:

```text
python -B -m unittest wallex.ai_provider.test_client -q
python -B -m unittest discover -s wallex -p "test_*.py" -q
```

All package tests use synthetic credentials, injected transports, or mocked HTTP
openers. They perform no live BoxAPI, market, or account requests.

## Availability and integration dependencies

The owner reported one successful call using `gpt-5.4-mini` at
`https://ai.boxapi.ir/api/v1/chat/completions`, followed by a request-limit 403
despite apparent wallet credit. Availability and quota remain **unverified**.
No live provider request was made for this implementation. The alternative
`/ai/v1/chat/completions` route is deliberately not used.

The request uses `messages`, `max_tokens=512`, and JSON-object `response_format`.
Provider support for these options and real response envelopes remains unverified
until separately authorized testing. Unsupported options fail closed through
normal HTTP handling. Credentials are explicitly injected and never loaded from
environment variables, files, or DPAPI. Production credential provisioning,
freshness/attestation mapping, total request deadlines, remain production integration limitations. Live trading safeguards remain unchanged.

## Integrated TP-1 research path

`python -B -m wallex.ai_provider.tp1_review --state-dir PATH --source-commit FULL_SHA --review-ai`
reuses validated public sources and the existing offline TP-1 Shadow pipeline.
One candidate call maximum per invocation; no candidate means no key prompt or
HTTP. Keys use hidden getpass only. Decimal evidence is exact strings, risk and
credit remain UNVERIFIED, and all actions remain NO_TRADE. Structured REVIEW
retention/veto counts, provider status, model, hashes and timestamps are recorded
in the public audit partition; no prose, headers or private account data are
published. Decision Center displays evidence on refresh. Synthetic transports
are explicitly labelled; actual inference is unavailable until owner verification.
The old client gate and production execution are not activated by this path.
