"""Explicit opt-in GET-only Trade Credit collection; no risk/credential approval.

No environment/files/credentials are read by default. Only an injected resolver
can provide a secret after explicit read-scope attestation. Never log requests.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import json
from types import MappingProxyType
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener
import ssl

from wallex.strategy.engine import Signal
from .credit_account import (
    AccountKind, CreditAccountBinding, CreditEvidenceInputs, CreditResponse, CreditSemantics,
    user_id_format, user_id_matches,
)

ORIGIN = "https://api.wallex.ir"
GET_PATHS = ("/sub-accounts", "/v1/prop/user/packages/active", "/v1/account/balances-detail")


class CredentialScope(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    TRADE_CREDIT = "TRADE_CREDIT"


@dataclass(frozen=True)
class ReadOnlyConfig:
    """Future LOCAL read verification only; declarations do not prove key scope.

    credential_reference is an opaque resolver label, never the API key itself.
    No trading/withdrawal permission or environment-variable lookup is supported.
    """
    credential_reference: str = field(repr=False)
    credential_sub_account_client_id: str | None = None
    scope: CredentialScope = CredentialScope.UNVERIFIED
    read_permission_confirmed: bool = False
    opt_in: bool = False
    timeout_seconds: int = 5
    max_accounts: int = 100
    max_response_bytes: int = 65536


@dataclass(frozen=True)
class HttpGet:
    path: str
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: int
    max_response_bytes: int
    method: str = field(default="GET", init=False)


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes = field(repr=False)


class GetTransport(Protocol):
    def get(self, request: HttpGet) -> HttpReply: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward authentication to a redirect target.


class UrllibGetTransport:
    """Optional future transport: fixed HTTPS origin/routes, TLS verification.

    No ambient proxy, redirect, retry, write method or reusable request history.
    Construction/import performs no HTTP. Tests must inject a mock transport.
    """
    def get(self, request: HttpGet) -> HttpReply:
        if (type(request) is not HttpGet or request.path not in GET_PATHS
                or type(request.timeout_seconds) is not int or not 1 <= request.timeout_seconds <= 60
                or type(request.max_response_bytes) is not int or not 1 <= request.max_response_bytes <= 1048576
                or set(request.headers) != {"x-api-key", "sub-account-client-id"}
                or not all(_label(v) for v in request.headers.values())):
            raise ValueError("Invalid read-only request")
        opener = build_opener(ProxyHandler({}), _NoRedirect(),
                              HTTPSHandler(context=ssl.create_default_context()))
        wire = Request(ORIGIN + request.path, headers=dict(request.headers), method="GET")
        try:
            with opener.open(wire, timeout=request.timeout_seconds) as response:
                return HttpReply(response.status, response.read(request.max_response_bytes + 1))
        except HTTPError as error:
            status = error.code
            error.close()
            return HttpReply(status, b"")  # Never retain/log an error body or headers.
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError("Read timeout") from None
            raise OSError("Read transport failed") from None


@dataclass(frozen=True)
class SanitizedRead:
    sub_account_client_id: str
    observed_at: int
    payload_json: str = field(repr=False)

    def response(self) -> CreditResponse:
        # A fresh mutable payload is provided for CREDIT-02's stable-in-call API.
        return CreditResponse(json.loads(self.payload_json), self.sub_account_client_id,
                              self.observed_at, True)


@dataclass(frozen=True)
class CreditReadBundle:
    binding: CreditAccountBinding
    accounts: SanitizedRead
    plan: SanitizedRead
    balances: SanitizedRead

    def for_assessment(self, *, semantics: CreditSemantics, as_of: int,
                       max_age_seconds: int) -> CreditEvidenceInputs:
        """Explicit separate unapproved semantics; no automatic risk evaluation."""
        return CreditEvidenceInputs(self.binding, self.accounts.response(), self.plan.response(),
                                    self.balances.response(), semantics, as_of, max_age_seconds)


class ReadReason(str, Enum):
    COLLECTED = "COLLECTED"
    SCOPE_DIAGNOSED = "SCOPE_DIAGNOSED"
    PROP_DIAGNOSED = "PROP_DIAGNOSED"
    PACKAGE_EVIDENCE_ABSENT = "PACKAGE_EVIDENCE_ABSENT"
    INACTIVE_OR_SUSPENDED = "INACTIVE_OR_SUSPENDED"
    DISABLED = "DISABLED"
    UNVERIFIED_SCOPE = "UNVERIFIED_SCOPE"
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_BINDING = "INVALID_BINDING"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INCOMPLETE_DISCOVERY = "INCOMPLETE_DISCOVERY"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"
    SENSITIVE_RESPONSE = "SENSITIVE_RESPONSE"
    INVALID_OBSERVATION_TIME = "INVALID_OBSERVATION_TIME"


@dataclass(frozen=True)
class CreditReadResult:
    collected: bool
    reason: ReadReason
    bundle: CreditReadBundle | None = None
    failed_endpoint: str | None = None
    http_status: int | None = None
    scope_checks: Mapping[str, object] | None = None
    diagnostic_classification: str | None = None
    credential_scope_verified: bool = field(default=False, init=False)
    executable_ready: bool = field(default=False, init=False)
    executable_action: Signal = field(default=Signal.NO_TRADE, init=False)


class _ReadFailure(ValueError):
    def __init__(self, reason, status=None, classification=None):
        self.reason, self.status, self.classification = reason, status, classification


def _require(ok, reason, classification=None):
    if not ok:
        raise _ReadFailure(reason, classification=classification)


def _label(value):
    return (type(value) is str and 0 < len(value) <= 512
            and all(33 <= ord(c) <= 126 for c in value))


def _int(value):
    return type(value) is int and value >= 0


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Nonfinite JSON")


def _pick(record, names):
    _require(type(record) is dict and all(name in record for name in names), ReadReason.INVALID_RESPONSE)
    return {name: record[name] for name in names}


def _contains(value, secret):
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(_contains(k, secret) or _contains(v, secret) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains(v, secret) for v in value)
    return False


def _sanitize(payload, path, binding, config):
    _require(type(payload) is dict and payload.get("success") is True and "result" in payload,
             ReadReason.INVALID_RESPONSE)
    result = payload["result"]
    if path == GET_PATHS[0]:
        _require(type(result) is list and 0 < len(result) <= config.max_accounts,
                 ReadReason.INCOMPLETE_DISCOVERY)
        info = _pick(payload.get("result_info"), ("page", "count", "total_count"))
        # The audited discovery operation documents no page query parameters.
        # One-page budget: reject partial pages instead of guessing retrieval.
        _require(all(_int(v) for v in info.values()) and info["page"] == 1
                 and info["count"] == info["total_count"] == len(result), ReadReason.INCOMPLETE_DISCOVERY)
        rows = [_pick(row, ("clientId", "userId", "type", "isActive", "isSuspend")) for row in result]
        ids = [row["clientId"] for row in rows]
        _require(all(_label(i) for i in ids) and len(set(ids)) == len(ids),
                 ReadReason.AMBIGUOUS_SCOPE, "AMBIGUOUS_DISCOVERY_IDENTITIES")
        matches = [r for r in rows if r["clientId"] == binding.sub_account_client_id]
        _require(len(matches) == 1, ReadReason.AMBIGUOUS_SCOPE, "ACCOUNT_IDENTITY_NOT_FOUND")
        _require(user_id_matches(matches[0]["userId"], binding.owner_user_id),
                 ReadReason.AMBIGUOUS_SCOPE, "OWNER_IDENTITY_MISMATCH")
        _require(matches[0]["type"] == "prop", ReadReason.AMBIGUOUS_SCOPE, "ACCOUNT_TYPE_MISMATCH")
        _require(type(matches[0]["isActive"]) is bool and type(matches[0]["isSuspend"]) is bool,
                 ReadReason.INVALID_RESPONSE, "MALFORMED_ACCOUNT_STATUS")
        _require(matches[0]["isActive"] is True and matches[0]["isSuspend"] is False,
                 ReadReason.INACTIVE_OR_SUSPENDED, "ACCOUNT_INACTIVE_OR_SUSPENDED")
        return {"success": True, "result": rows, "result_info": info}
    if path == GET_PATHS[1]:
        # Empty evidence is not proof that no active plan exists at the provider.
        _require(not (result is None or result == {} or (type(result) is dict
                 and set(result) == {"packageDetail", "userPackageDetail"}
                 and result["packageDetail"] is None and result["userPackageDetail"] is None)),
                 ReadReason.PACKAGE_EVIDENCE_ABSENT, "ACTIVE_PACKAGE_EVIDENCE_ABSENT")
        _require(type(result) is dict, ReadReason.INVALID_RESPONSE)
        package = _pick(result.get("packageDetail"), (
            "id", "currency", "destination_currency", "assurance", "loan", "dailyFee",
            "dailyFeeDiscount", "dailyFeeAfterDiscount"))
        active = _pick(result.get("userPackageDetail"), (
            "id", "type", "status", "createdAt", "expireAt", "finalPayback",
            "finalPayableFeeAmount", "warningLimit", "liquidityLimit"))
        _require(type(package["id"]) is int and package["id"] == binding.package_id,
                 ReadReason.AMBIGUOUS_SCOPE, "PACKAGE_IDENTITY_MISMATCH")
        _require(type(active["id"]) is int and active["id"] == binding.user_package_id,
                 ReadReason.AMBIGUOUS_SCOPE, "USER_PACKAGE_IDENTITY_MISMATCH")
        return {"success": True, "result": {"packageDetail": package, "userPackageDetail": active}}
    _require(type(result) is dict and bool(result), ReadReason.INVALID_RESPONSE)
    return {"success": True, "result": {currency: _pick(row, ("symbol", "total", "freeze", "available"))
                                        for currency, row in result.items()}}


def _scope_checks(payload, binding, config):
    """One bounded page, categorical diagnostics only; no account approval."""
    _require(type(payload) is dict and payload.get("success") is True, ReadReason.INVALID_RESPONSE)
    rows = payload.get("result")
    _require(type(rows) is list and len(rows) <= config.max_accounts, ReadReason.INCOMPLETE_DISCOVERY)
    info = _pick(payload.get("result_info"), ("page", "count", "total_count"))
    _require(all(_int(v) for v in info.values()) and info["page"] == 1
             and info["count"] == info["total_count"] == len(rows), ReadReason.INCOMPLETE_DISCOVERY)
    rows = [_pick(row, ("clientId", "userId", "type", "isActive", "isSuspend")) for row in rows]
    ids = [row["clientId"] for row in rows]
    _require(all(_label(value) for value in ids), ReadReason.INVALID_RESPONSE)
    matches = [row for row in rows if row["clientId"] == binding.sub_account_client_id]
    selected = matches[0] if len(matches) == 1 else None
    # Never echo arbitrary provider strings that could contain secrets or IDs.
    safe_types = ("prop", "spot", "main", "margin", "credit", "trade_credit")
    account_type = selected["type"] if selected and selected["type"] in safe_types else "OTHER_OR_UNKNOWN"
    return MappingProxyType({
        "client_id_match": "UNIQUE" if len(matches) == 1 else "MULTIPLE" if matches else "NOT_FOUND",
        "user_id_format": user_id_format(selected["userId"] if selected else None),
        "user_id_match": user_id_matches(selected["userId"], binding.owner_user_id) if selected else None,
        "account_type": account_type if selected else "UNAVAILABLE",
        "is_active": selected["isActive"] if selected and type(selected["isActive"]) is bool else None,
        "is_suspended": selected["isSuspend"] if selected and type(selected["isSuspend"]) is bool else None,
        "duplicate_client_ids": len(ids) != len(set(ids)),
        "ambiguous_matches": len(matches) > 1,
    })


class CreditReadOnlyClient:
    def __init__(self, *, transport: GetTransport, resolve_credential: Callable[[str], str],
                 observation_clock: Callable[[], int]):
        self._transport = transport
        self._resolve = resolve_credential
        self._clock = observation_clock

    def fetch(self, binding: CreditAccountBinding, *, config: ReadOnlyConfig,
              diagnose_scope: bool = False, diagnose_prop: bool = False) -> CreditReadResult:
        """Collect exactly three whitelisted GETs or return no usable bundle.

        Only explicit future opt-in and owner-attested matching read scope permit
        resolver/transport use. These declarations never verify actual key scope.
        All failures discard partial evidence; there are no retries or fallbacks.
        diagnose_scope stops after discovery; diagnose_prop stops after the active
        package. Both return categorical diagnostics only, never a partial bundle.
        """
        endpoint = None
        http_status = None
        checks = None
        try:
            _require(type(config) is ReadOnlyConfig and type(diagnose_scope) is bool
                     and type(diagnose_prop) is bool and not (diagnose_scope and diagnose_prop),
                     ReadReason.INVALID_CONFIG)
            _require(config.opt_in is True, ReadReason.DISABLED)
            _require(type(binding) is CreditAccountBinding and binding.kind is AccountKind.TRADE_CREDIT
                     and _label(binding.sub_account_client_id) and _int(binding.owner_user_id)
                     and _int(binding.package_id) and binding.package_id > 0
                     and _int(binding.user_package_id) and binding.user_package_id > 0, ReadReason.INVALID_BINDING)
            _require(config.scope is CredentialScope.TRADE_CREDIT and config.read_permission_confirmed is True
                     and config.credential_sub_account_client_id == binding.sub_account_client_id,
                     ReadReason.UNVERIFIED_SCOPE)
            _require(_label(config.credential_reference) and type(config.timeout_seconds) is int
                     and 1 <= config.timeout_seconds <= 60 and type(config.max_accounts) is int
                     and 1 <= config.max_accounts <= 1000 and type(config.max_response_bytes) is int
                     and 1 <= config.max_response_bytes <= 1048576, ReadReason.INVALID_CONFIG)
            try:
                secret = self._resolve(config.credential_reference)
            except Exception:
                raise _ReadFailure(ReadReason.CREDENTIAL_UNAVAILABLE) from None
            _require(_label(secret), ReadReason.CREDENTIAL_UNAVAILABLE)
            reads = []
            previous = None
            for endpoint in (GET_PATHS[:1] if diagnose_scope else GET_PATHS[:2] if diagnose_prop else GET_PATHS):
                http_status = None
                request = HttpGet(endpoint, MappingProxyType({
                    "x-api-key": secret, "sub-account-client-id": binding.sub_account_client_id,
                }), config.timeout_seconds, config.max_response_bytes)
                try:
                    reply = self._transport.get(request)
                except TimeoutError:
                    raise _ReadFailure(ReadReason.TIMEOUT) from None
                except Exception:
                    raise _ReadFailure(ReadReason.TRANSPORT_ERROR) from None
                _require(type(reply) is HttpReply and type(reply.status) is int, ReadReason.INVALID_RESPONSE)
                http_status = reply.status
                if reply.status != 200:
                    reason = {401: ReadReason.AUTHENTICATION_FAILED, 403: ReadReason.FORBIDDEN,
                              404: ReadReason.NOT_FOUND, 429: ReadReason.RATE_LIMITED}.get(reply.status,
                              ReadReason.SERVER_ERROR if 500 <= reply.status <= 599 else ReadReason.HTTP_ERROR)
                    classification = None
                    if endpoint == GET_PATHS[1]:
                        classification = {401: "PROP_PACKAGE_AUTH_REJECTED", 403: "PROP_PACKAGE_ACCESS_DENIED",
                                          404: "PROP_PACKAGE_HTTP_NOT_FOUND"}.get(reply.status)
                    raise _ReadFailure(reason, reply.status, classification)
                try:
                    observed = self._clock()
                except Exception:
                    raise _ReadFailure(ReadReason.INVALID_OBSERVATION_TIME) from None
                _require(_int(observed) and (previous is None or observed >= previous),
                         ReadReason.INVALID_OBSERVATION_TIME)
                previous = observed
                _require(type(reply.body) is bytes, ReadReason.INVALID_RESPONSE)
                _require(len(reply.body) <= config.max_response_bytes, ReadReason.RESPONSE_TOO_LARGE)
                try:
                    payload = json.loads(reply.body.decode("utf-8"), parse_float=Decimal,
                                         parse_constant=_constant, object_pairs_hook=_unique)
                    if diagnose_scope or (diagnose_prop and endpoint == GET_PATHS[0]):
                        candidate_checks = _scope_checks(payload, binding, config)
                        _require(not _contains(dict(candidate_checks), secret), ReadReason.SENSITIVE_RESPONSE)
                        checks = candidate_checks
                        if diagnose_scope:
                            return CreditReadResult(False, ReadReason.SCOPE_DIAGNOSED,
                                                    http_status=200, scope_checks=checks)
                    sanitized = _sanitize(payload, endpoint, binding, config)
                    _require(not _contains(sanitized, secret), ReadReason.SENSITIVE_RESPONSE)
                    encoded = json.dumps(sanitized, ensure_ascii=False, allow_nan=False, sort_keys=True)
                except _ReadFailure:
                    raise
                except Exception:
                    raise _ReadFailure(ReadReason.INVALID_RESPONSE) from None
                reads.append(SanitizedRead(binding.sub_account_client_id, observed, encoded))
            if diagnose_prop:
                return CreditReadResult(False, ReadReason.PROP_DIAGNOSED, http_status=200,
                                        scope_checks=checks, diagnostic_classification="PROP_PACKAGE_READABLE")
            return CreditReadResult(True, ReadReason.COLLECTED, CreditReadBundle(binding, *reads))
        except _ReadFailure as error:
            return CreditReadResult(False, error.reason, failed_endpoint=endpoint,
                                    http_status=error.status if error.status is not None else http_status,
                                    scope_checks=checks, diagnostic_classification=error.classification or error.reason.value)
        except Exception:
            return CreditReadResult(False, ReadReason.INVALID_CONFIG, failed_endpoint=endpoint)
