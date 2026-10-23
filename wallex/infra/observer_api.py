"""Public observer projection only: no account API, credentials or order controls."""

import argparse
from contextlib import closing
from datetime import datetime
from decimal import Decimal
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from wallex.audit.models import AUDIT_SCHEMA_VERSION
from wallex.market.datasets import _unique_object
from wallex.market.evaluation import canonical_json
from .agent_runtime import MODE, PARTITION, BLOCKERS, ObserverConfig, run_observer

MAX_JSON = 1048576


def _revision():
    value = os.environ.get("QF_SOURCE_COMMIT", "")
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _object(value):
    return value if type(value) is dict else {}


def _stamp(value):
    return value if type(value) is int and 0 <= value <= 253402300799 else None


def _codes(value):
    return [v for v in value[:100] if type(v) is str and re.fullmatch(r"[A-Za-z0-9_:[\]./-]{1,160}", v)] if type(value) is list else []


def _count(value):
    return value if type(value) is int and 0 <= value <= 100000 else None


def _price(value):
    return value if type(value) is str and len(value) <= 200 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value) else None


def _decode(value):
    return json.loads(value, parse_float=Decimal, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("INVALID_JSON")))


def _technical(value):
    value = _object(value)
    return {"eligible": value.get("eligible") if type(value.get("eligible")) is bool else None,
            "reasons": _codes(value.get("reasons"))}


def _observation(raw, symbol):
    if (type(raw) is not dict or raw.get("mode") != MODE or raw.get("action") != "NO_TRADE"
            or raw.get("executable_ready") is not False or raw.get("symbol") != symbol):
        raise ValueError("UNSAFE_OBSERVER_RECORD")
    pipeline, book = _object(raw.get("pipeline")), _object(raw.get("orderbook"))
    validation, summary = _object(pipeline.get("validation")), _object(pipeline.get("summary"))
    source_steps = pipeline.get("evaluated_steps", [])
    if type(source_steps) is not list or len(source_steps) > 128:
        raise ValueError("INVALID_STEP_BUDGET")
    steps = []
    for step in source_steps:
        step = _object(step)
        technical = _object(step.get("technical_evidence"))
        steps.append({"as_of": _stamp(step.get("as_of")), "action": "NO_TRADE", "executable_ready": False,
                      "technical_eligible": step.get("technical_eligible") is True,
                      "blockers": _codes(step.get("blockers")),
                      "technical_evidence": {key: _technical(technical.get(key)) for key in ("trend", "pullback", "stop")}})
    requests = _object(raw.get("requests"))
    hashes = {name: row["raw_sha256"] for name, row in ((name, _object(requests.get(name))) for name in ("m1", "h1"))
              if type(row.get("raw_sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", row["raw_sha256"])}
    if type(book.get("raw_sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", book["raw_sha256"]):
        hashes["depth"] = book["raw_sha256"]
    return {"mode": MODE, "action": "NO_TRADE", "executable_ready": False, "symbol": symbol,
            "status": "CAPTURED_PUBLIC_OBSERVATION", "observed_at": _stamp(raw.get("observed_at")),
            "decision_cutoff": _stamp(raw.get("decision_cutoff")),
            "health": raw.get("health") if raw.get("health") in ("RUNNING", "DEGRADED") else "DEGRADED",
            "source_hashes": hashes, "source_event_freshness": "UNAVAILABLE",
            "technical_mode": "HISTORICAL_DIAGNOSTICS_ON_PUBLIC_CLOSED_CANDLES",
            "volume_assumption": "CALLER_ATTESTED_BASE" if raw.get("volume_unit") == "base" else "UNVERIFIED",
            "orderbook": {key: _price(book.get(key)) for key in ("best_bid", "best_ask", "midpoint", "spread_pct")},
            "pipeline": {"validation": {"h1_candles": _count(validation.get("h1_candles")),
                                       "m1_m15": {"complete_m15": _count(_object(validation.get("m1_m15")).get("complete_m15"))}},
                         "summary": {key: _count(summary.get(key)) for key in ("evaluated_steps", "technical_eligible_steps", "replay_errors", "insufficient_data_steps")},
                         "evaluated_steps": steps},
            "observation_failures": _codes(raw.get("observation_failures")),
            "readiness_blockers": list(dict.fromkeys([*BLOCKERS, *_codes(raw.get("readiness_blockers"))]))}


def _ai_projection(raw, symbol, now):
    """Allowlist public technical metadata only; never prose/provider bodies."""
    if (type(raw) is not dict or raw.get("symbol") != symbol or raw.get("action") != "NO_TRADE"
            or raw.get("executable_ready") is not False or type(raw.get("records")) is not list
            or len(raw["records"]) > 5):
        return None
    records = []
    for row in raw["records"]:
        if type(row) is not dict or row.get("action") != "NO_TRADE" or row.get("executable_ready") is not False:
            continue
        stamps = [row.get(k) for k in ("as_of", "requested_at", "completed_at")]
        if (any(_stamp(v) is None for v in stamps) or not stamps[0] <= stamps[1] <= stamps[2] <= now
                or row.get("inference_source") not in ("BOXAPI_HTTPS", "SYNTHETIC_TEST_TRANSPORT")
                or row.get("evidence_kind") not in ("historical_data", "synthetic_fixture")
                or re.fullmatch(r"[0-9a-f]{64}", str(row.get("evidence_hash"))) is None
                or re.fullmatch(r"[0-9a-f]{40}", str(row.get("source_commit"))) is None
                or row.get("advisory_decision") not in ("REVIEW", "NO_TRADE")):
            continue
        models = {k: row.get(k) if type(row.get(k)) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,95}", row[k]) else None
                  for k in ("requested_model", "response_model")}
        allowed = {"OK", "TIMEOUT", "AUTHENTICATION_FAILED", "FORBIDDEN", "RATE_LIMITED", "HTTP_ERROR",
                   "REQUEST_LIMIT_REACHED", "INVALID_RESPONSE", "TRANSPORT_ERROR", "INVALID_CONTEXT"}
        status = row.get("status") if row.get("status") in allowed else "INVALID_RESPONSE"
        from wallex.ai_provider.models import REASONS
        allowed_codes = set().union(*REASONS.values(), allowed)
        records.append({**models, "status": status, "reason_codes": [v for v in _codes(row.get("reason_codes")) if v in allowed_codes],
            **{k: row[k] for k in ("as_of", "requested_at", "completed_at", "evidence_hash", "source_commit", "inference_source", "evidence_kind")},
            "advisory_decision": row["advisory_decision"], "action": "NO_TRADE", "executable_ready": False})
    return {"records": records, "action": "NO_TRADE", "executable_ready": False,
            "decision_cutoff": _stamp(raw.get("decision_cutoff")), "status": "RECORDED_RESEARCH_ONLY" if records else "UNAVAILABLE"}


def snapshot(state_dir, *, now=None):
    """Read selected public-partition events, never initialize/mutate Ledger.

    Preflight/account events are excluded in SQL and all returned fields are
    explicitly projected. Database paths, trace IDs and raw payloads are omitted.
    """
    now = int(time.time()) if now is None else now
    result = {"mode": MODE, "status": "PERSISTED_STATUS_NOT_LIVENESS_PROOF", "api_schema": 1,
              "action": "NO_TRADE", "executable_ready": False, "generated_at": now, "source_commit": _revision(),
              "data_status": "UNAVAILABLE", "observation_age_seconds": None,
              "last_observation": None, "ai_review": None, "runtime": {"state": "UNAVAILABLE", "heartbeat_at": None,
                  "heartbeat_age_seconds": None, "process_liveness": "UNVERIFIED"},
              "blockers": list(BLOCKERS), "private_account_api": "NOT_EXPOSED_REQUIRES_AUTHENTICATED_BACKEND"}
    try:
        state = Path(state_dir).resolve()
        marker = state / "observer.json"
        if marker.stat().st_size > 4096:
            raise ValueError("INVALID_OBSERVER_BINDING")
        binding = _decode(marker.read_text(encoding="utf-8"))
        config = ObserverConfig(state, binding["symbol"], binding["base_asset"], binding["quote_asset"])
        config.validate()
        if binding != config.binding():
            raise ValueError("INVALID_OBSERVER_BINDING")
        with closing(sqlite3.connect((state / "audit.sqlite").as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            version = db.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
            if version != (AUDIT_SCHEMA_VERSION,):
                raise ValueError("INVALID_LEDGER_SCHEMA")
            def latest(kinds):
                placeholders = ','.join('?' for _ in kinds)
                row = db.execute("SELECT payload_json,occurred_at,run_id FROM audit_events WHERE account_id=? AND kind IN (" + placeholders + ") ORDER BY event_seq DESC LIMIT 1",
                                 (PARTITION, *kinds)).fetchone()
                if row is None:
                    return None
                if len(row[0]) > MAX_JSON:
                    raise ValueError("OBSERVER_RECORD_TOO_LARGE")
                return _decode(row[0]), int(datetime.fromisoformat(row[1]).timestamp()), row[2]
            ai = latest(("public_tp1_ai_review",))
            observation = latest(("observer_cycle_completed",))
            heartbeat = latest(("observer_heartbeat",))
            lifecycle = latest(("observer_started", "observer_stopped", "observer_failed"))
        if ai:
            result["ai_review"] = _ai_projection(ai[0], config.symbol, now)
        if observation:
            result["last_observation"] = _observation(observation[0], config.symbol)
            observed = result["last_observation"]["observed_at"]
            if observed is None or observed > now:
                raise ValueError("INVALID_OBSERVATION_TIME")
            result["observation_age_seconds"] = now - observed
            result["data_status"] = "RECENT_CAPTURED" if now - observed <= 1200 else "STALE"
        if heartbeat:
            at = heartbeat[1]
            result["runtime"].update(heartbeat_at=at, heartbeat_age_seconds=now-at if at <= now else None)
        if lifecycle:
            state = "STARTING" if lifecycle[0].get("status") == "STARTING" else lifecycle[0].get("status")
            if state in ("STARTING", "STOPPED", "FAILED"):
                result["runtime"]["state"] = state
            if state == "STARTING" and heartbeat and heartbeat[2] == lifecycle[2] and heartbeat[1] >= lifecycle[1] and heartbeat[0].get("status") in ("RUNNING", "DEGRADED"):
                result["runtime"]["state"] = heartbeat[0]["status"]
        if result["data_status"] != "RECENT_CAPTURED":
            result["blockers"].append("OBSERVER_DATA_" + result["data_status"])
    except Exception:
        result.update(data_status="UNAVAILABLE", last_observation=None, observation_age_seconds=None)
        result["blockers"].append("OBSERVER_STATE_UNAVAILABLE_OR_INVALID")
    return result


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, state_dir, frontend_dir=None):
        self.state_dir = Path(state_dir)
        self.frontend_dir = Path(frontend_dir).resolve() if frontend_dir else None
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "QuantForgePublicReadOnly"
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # Never log URLs, headers, bodies or account input.

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        target = urlsplit(self.path)
        if target.fragment or (target.path.startswith("/api/") and target.query):
            return self._send(404, b'{"error":"NOT_FOUND"}')
        if target.path == "/api/observer":
            data = snapshot(self.server.state_dir)
            return self._send(200 if data["last_observation"] is not None else 503, canonical_json(data).encode())
        if target.path == "/api/health":
            return self._send(200, canonical_json({"status":"API_UP","source_commit":_revision(),"action":"NO_TRADE","executable_ready":False}).encode())
        root = self.server.frontend_dir
        path = root / unquote(target.path).lstrip('/') if root else None
        if root and target.path == "/":
            path = root / "index.html"
        allowed = {".html", ".js", ".css", ".svg", ".woff", ".woff2", ".png", ".ico"}
        if path and path.resolve().is_relative_to(root) and not any(part.startswith('.') for part in Path(unquote(target.path)).parts) and path.suffix in allowed and path.is_file() and path.stat().st_size <= 16777216:
            return self._send(200, path.read_bytes(), mimetypes.guess_type(path)[0] or "application/octet-stream")
        self._send(404, b'{"error":"NOT_FOUND"}')

    do_HEAD = do_GET
    def _deny(self):
        self.close_connection = True
        self._send(405, b'{"error":"READ_ONLY","action":"NO_TRADE","executable_ready":false}')
    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _deny


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--fetch-public", action="store_true", help="Explicit opt-in to run existing public observer alongside API")
    for key in ("symbol", "base", "quote", "volume-unit"):
        parser.add_argument("--"+key)
    args=parser.parse_args(argv)
    stop, worker = threading.Event(), None
    server=Server((args.host,args.port),args.state_dir,args.frontend_dir)
    if args.fetch_public:
        config=ObserverConfig(args.state_dir,args.symbol,args.base,args.quote,args.volume_unit)
        config.validate()
        def observe():
            try:
                run_observer(config,fetch_public=True,stop=stop,emit=lambda _:None)
            except Exception:
                print('{"worker":"STOPPED_FAIL_CLOSED","action":"NO_TRADE"}',flush=True)
        worker=threading.Thread(target=observe,daemon=True)
        worker.start()
    def interrupt(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,interrupt)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if worker:
            worker.join(timeout=40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
