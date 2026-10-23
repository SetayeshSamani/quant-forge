"""Supervised real-public-market observer; live execution is unavailable, not enabled."""

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from hashlib import sha256
import json
from pathlib import Path
import re
import signal
import threading
import time
from urllib.error import URLError
from urllib.parse import urlencode
import uuid

from wallex.audit.ledger import Ledger, LedgerError
from wallex.audit.writer import WriterBusy
from wallex.market.evaluation import EvidenceKind, canonical_json
from wallex.market import history, public_orderbook
from wallex.shadow.offline_tp1 import run_offline
from . import credit_preflight
from .runtime import Runtime, RuntimeState

MODE = "PUBLIC_OBSERVATION_ONLY"
# Ledger's required account_id is used ONLY as an explicitly non-account journal
# partition. It is never mapped to an exchange account or passed to an executor.
PARTITION = "PUBLIC_OBSERVER_NOT_AN_EXCHANGE_ACCOUNT"
BLOCKERS = (
    "REAL_MONEY_ACTIVATION_NOT_APPROVED", "RISK_PIPELINE_DIAGNOSTIC_ONLY",
    "LIVE_GATEWAY_NOT_ACTIVATED", "LIVE_ACCOUNT_LEDGER_NOT_ACTIVATED",
    "LIVE_PROTECTIVE_EXIT_POLICY_NOT_ACTIVATED", "VENUE_FILL_SNAPSHOT_COMPLETENESS_UNVERIFIED",
    "PRIVATE_BALANCES_POSITIONS_OPEN_ORDERS_UNVERIFIED", "CREDIT_PLAN_UNVERIFIED",
    "ACTUAL_FEES_MARKET_RULES_AND_PERMISSION_UNVERIFIED", "PE_UNAVAILABLE",
    "MARKET_IDENTITY_AND_EVENT_FRESHNESS_UNVERIFIED",
)


def _now():
    return time.time_ns() // 1000000000


def _stamp(value):
    if type(value) is not int or value < 264 * 3600:
        raise ValueError("INVALID_OBSERVER_CLOCK")
    return value


def _safe(code, **fields):
    return {"mode": MODE, "status": code, **fields, "action": "NO_TRADE", "executable_ready": False}


@dataclass(frozen=True)
class ObserverConfig:
    state_dir: Path
    symbol: str
    base_asset: str
    quote_asset: str
    volume_unit: str | None = None
    interval_seconds: int = 60

    def validate(self):
        if (any(type(value) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", value) is None
                for value in (self.symbol, self.base_asset, self.quote_asset))
                or self.base_asset == self.quote_asset or self.symbol != self.base_asset + self.quote_asset):
            raise ValueError("INVALID_INSTRUMENT")
        if self.volume_unit not in (None, "base") or type(self.interval_seconds) is not int or not 60 <= self.interval_seconds <= 900:
            raise ValueError("INVALID_OBSERVER_CONFIG")
        path = Path(self.state_dir).resolve()
        if path.is_relative_to(Path(__file__).resolve().parents[2]) and not any(path.is_relative_to(Path(__file__).resolve().parents[2]/p) for p in ("data", ".local")):
            raise ValueError("STATE_MUST_BE_OUTSIDE_REPOSITORY")
        return path

    def binding(self):
        return {"version": 1, "mode": MODE, "symbol": self.symbol,
                "base_asset": self.base_asset, "quote_asset": self.quote_asset}


def _state(config):
    path = config.validate()
    marker = path / "observer.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != config.binding():
            raise ValueError("STATE_BINDING_MISMATCH")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("DEDICATED_STATE_DIRECTORY_REQUIRED")
        path.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(config.binding()) + "\n")
    return path, Ledger(path / "audit.sqlite")


def _audit(ledger, run_id, kind, payload, at):
    return ledger.record_event(kind, account_id=PARTITION, run_id=run_id,
                               trace_id=run_id, at=datetime.fromtimestamp(_stamp(at), timezone.utc), payload=payload)


def _failure(error):
    text = str(error)
    code = getattr(getattr(error, "code", None), "value", None)
    if type(code) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", code):
        return code
    if isinstance(error, WriterBusy):
        return "OBSERVER_WRITER_BUSY"
    if isinstance(error, LedgerError):
        return "AUDIT_STORAGE_UNAVAILABLE"
    if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", text):
        return text
    if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
        return "PUBLIC_NETWORK_TIMEOUT"
    if isinstance(error, (OSError, URLError)):
        return "PUBLIC_NETWORK_ERROR"
    return "PUBLIC_DATA_VALIDATION_FAILED"


def observe_cycle(config, directory, *, fetch_public=False, history_transport=None,
                  book_transport=None, clock=_now, evidence_kind=EvidenceKind.HISTORICAL_DATA):
    """At most three public GET attempts; unchanged TP-1/Shadow/Risk composition.

    Candle cutoff and actual receipt times are separate. Recomputed historical
    steps are never promoted into live market/risk evidence. No private client,
    credentials, order adapter or execution request is used in this function.
    """
    config.validate()
    if fetch_public is not True:
        raise ValueError("EXPLICIT_PUBLIC_FETCH_REQUIRED")
    started = _stamp(clock())
    cutoff = started // 900 * 900
    h1_end = cutoff // 3600 * 3600
    ranges = {"m1": (cutoff - 6 * 3600, cutoff, "1"),
              "h1": (h1_end - 264 * 3600, h1_end, "60")}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    requests, failures = {}, []
    for name, (start, end, resolution) in ranges.items():
        url = history.ENDPOINT + "?" + urlencode({"symbol": config.symbol, "resolution": resolution,
                                                  "from": start, "to": end - 1})
        entry = {"url": url, "request_started_at": _stamp(clock()), "received_at": None, "http_status": None}
        try:
            payload = (history_transport or history.public_get)(url, timeout_seconds=5)
            entry["received_at"] = _stamp(clock())
            if entry["received_at"] < entry["request_started_at"]:
                raise ValueError("LOCAL_CLOCK_REVERSED")
            if type(payload) is not bytes or len(payload) > history.MAX_BYTES:
                raise ValueError("INVALID_PUBLIC_RESPONSE_SIZE")
            entry["http_status"] = 200  # Existing public_get returns bytes only for HTTP 200.
            entry["raw_sha256"] = sha256(payload).hexdigest()
            entry["byte_count"] = len(payload)
            (directory / (name + ".json")).write_bytes(payload)
        except Exception as error:
            entry["reason"] = _failure(error)
            match = re.fullmatch(r"PUBLIC_HTTP_([0-9]{3})", entry["reason"])
            if match:
                entry["http_status"] = int(match[1])
            failures.append(name + ":" + entry["reason"])
        requests[name] = entry
    depth = public_orderbook.fetch_orderbook(symbol=config.symbol, fetch=True,
                                             timeout_seconds=5, max_levels=1000,
                                             transport=book_transport, clock=clock)
    finished = _stamp(clock())
    if finished < started or finished - started > 30:
        failures.append("COLLECTION_CLOCK_OR_AGE_INVALID")
    if finished // 900 * 900 != cutoff:
        failures.append("CUTOFF_ADVANCED_DURING_COLLECTION")
    pipeline = None
    if not failures:
        try:
            pipeline = run_offline(directory / "m1.json", directory / "h1.json",
                                   symbol=config.symbol, base_asset=config.base_asset, quote_asset=config.quote_asset,
                                   m1_start=ranges["m1"][0], m1_end=cutoff,
                                   h1_start=ranges["h1"][0], h1_end=h1_end, as_of=cutoff,
                                   volume_unit=config.volume_unit, evidence_kind=evidence_kind)
        except Exception as error:
            failures.append("pipeline:" + _failure(error))
    usable = pipeline is not None and pipeline["summary"]["evaluated_steps"] > 0 and depth["valid"] and not failures
    report = _safe("OBSERVED_BLOCKED" if usable else "OBSERVATION_DEGRADED",
                   health="RUNNING" if usable else "DEGRADED", symbol=config.symbol,
                   decision_cutoff=cutoff, observed_at=finished, requests=requests, orderbook=depth,
                   technical_mode="HISTORICAL_DIAGNOSTICS_ON_CURRENT_PUBLIC_READS",
                   volume_unit=config.volume_unit,
                   pipeline=pipeline, observation_failures=failures,
                   observation_complete=not failures and depth["valid"],
                   readiness_blockers=list(BLOCKERS),
                   account_evidence="UNVERIFIED_NOT_FETCHED", source_event_freshness="UNAVAILABLE")
    with (directory / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(canonical_json(report) + "\n")
    return report


def run_observer(config, *, fetch_public=False, once=False, max_cycles=None, stop=None,
                 clock=_now, history_transport=None, book_transport=None, emit=None,
                 evidence_kind=EvidenceKind.HISTORICAL_DATA):
    if fetch_public is not True:
        raise ValueError("EXPLICIT_PUBLIC_FETCH_REQUIRED")
    if type(once) is not bool or (max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1)):
        raise ValueError("INVALID_CYCLE_LIMIT")
    path, ledger = _state(config)
    stop = stop if stop is not None else threading.Event()
    emit = emit if emit is not None else lambda record: print(canonical_json(record), flush=True)
    run_id = "observer-" + uuid.uuid4().hex  # Local audit identity, never an exchange order ID.
    runtime = Runtime().transition(RuntimeState.STARTING)
    with ledger.writer(PARTITION):
        events = ledger.events()
        previous = [event.payload for event in events if event.kind == "observer_cycle_completed"
                    and event.payload["observation_complete"]]
        last = previous[-1] if previous else None
        _audit(ledger, run_id, "observer_started", _safe("STARTING", previous_public_cutoff=None if last is None else last["decision_cutoff"]), clock())
        cycles = 0
        try:
            while not stop.is_set():
                cutoff = _stamp(clock()) // 900 * 900
                if last is not None and cutoff < last["decision_cutoff"]:
                    raise ValueError("LOCAL_CLOCK_BEHIND_JOURNAL")
                if last is not None and cutoff == last["decision_cutoff"] and last.get("volume_unit") == config.volume_unit:
                    report = _safe("ALREADY_OBSERVED_NO_HTTP", decision_cutoff=cutoff,
                                   observed_at=_stamp(clock()), health=last["health"])
                else:
                    cycle_name = str(cutoff) + "-" + uuid.uuid4().hex
                    _audit(ledger, run_id, "observer_cycle_started", _safe("COLLECTING", decision_cutoff=cutoff), clock())
                    report = observe_cycle(config, path / "observations" / cycle_name, fetch_public=True,
                                           history_transport=history_transport, book_transport=book_transport,
                                           clock=clock, evidence_kind=evidence_kind)
                    _audit(ledger, run_id, "observer_cycle_completed", report, clock())
                    if report["observation_complete"]:
                        last = report
                target = RuntimeState(report["health"])
                if runtime.state is not target:
                    runtime = runtime.transition(target)
                _audit(ledger, run_id, "observer_heartbeat", _safe(runtime.state.value, observed_at=_stamp(clock()),
                                                                decision_cutoff=cutoff), clock())
                emit(report)
                cycles += 1
                if once or (max_cycles is not None and cycles >= max_cycles):
                    break
                stop.wait(config.interval_seconds)
        except Exception as error:
            _audit(ledger, run_id, "observer_failed", _safe("FAILED", failure=_failure(error)), clock())
            raise
        finally:
            runtime = runtime.transition(RuntimeState.STOPPING).transition(RuntimeState.STOPPED)
            _audit(ledger, run_id, "observer_stopped", _safe(runtime.state.value), clock())
    return report if cycles else _safe("STOPPED")


def preflight(config, arguments):
    """Delegate hidden interactive credential handling and strict identity checks.

    Only the existing sanitized summary is journaled. Neither this function nor
    the observer obtains usable equity/positions/fees or trading permission.
    """
    path, ledger = _state(config)
    output = StringIO()
    with ledger.writer(PARTITION):
        with redirect_stdout(output):
            try:
                parsed = credit_preflight._arguments(arguments)
                if parsed.currency != config.quote_asset:
                    raise ValueError("PREFLIGHT_QUOTE_MISMATCH")
                code = credit_preflight.main(arguments)
            except SystemExit as error:
                code = error.code
        report = json.loads(output.getvalue())
        report.update(mode="MANUAL_READ_ONLY_PREFLIGHT", readiness_blockers=list(BLOCKERS),
                      action="NO_TRADE", executable_ready=False)
        _audit(ledger, "preflight-" + uuid.uuid4().hex, "preflight_summary", report, _now())
    print(canonical_json(report))
    return code


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print(canonical_json(_safe("INVALID_ARGUMENTS")))
        raise SystemExit(2)


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("observe", "preflight", "status"):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, required=True)
        if name != "status":
            for label in ("symbol", "base", "quote"):
                command.add_argument("--" + label, required=True)
        if name == "observe":
            command.add_argument("--fetch-public", action="store_true", required=True)
            command.add_argument("--once", action="store_true")
            command.add_argument("--max-cycles", type=int)
            command.add_argument("--interval-seconds", type=int, default=60)
            command.add_argument("--volume-unit", choices=("base",))
        if name == "preflight":
            command.add_argument("credit_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            if not (args.state_dir / "observer.json").is_file() or not (args.state_dir / "audit.sqlite").is_file():
                raise ValueError("OBSERVER_STATE_NOT_FOUND")
            marker = json.loads((args.state_dir / "observer.json").read_text(encoding="utf-8"))
            if marker.get("mode") != MODE or marker.get("version") != 1:
                raise ValueError("STATE_BINDING_MISMATCH")
            events = tuple(event for event in Ledger(args.state_dir / "audit.sqlite").events()
                           if event.account_id == PARTITION)
            heartbeat = next((event for event in reversed(events) if event.kind == "observer_heartbeat"), None)
            latest = next((event for event in reversed(events) if event.kind == "observer_cycle_completed"), None)
            lifecycle = next((event for event in reversed(events) if event.kind in ("observer_started", "observer_stopped", "observer_failed")), None)
            account_check = next((event for event in reversed(events) if event.kind == "preflight_summary"), None)
            failure = next((event for event in reversed(events) if event.kind == "observer_failed"), None)
            print(canonical_json(_safe("PERSISTED_STATUS_NOT_LIVENESS_PROOF",
                                      last_lifecycle=None if lifecycle is None else lifecycle.payload,
                                      last_failure=None if failure is None else failure.payload,
                                      last_preflight=None if account_check is None else account_check.payload,
                                      preflight_age_seconds=None if account_check is None else _now() - int(account_check.occurred_at.timestamp()),
                                      heartbeat=None if heartbeat is None else heartbeat.payload,
                                      last_observation=None if latest is None else latest.payload,
                                      heartbeat_age_seconds=None if heartbeat is None else _now() - int(heartbeat.occurred_at.timestamp()))))
            return 0
        config = ObserverConfig(args.state_dir, args.symbol, args.base, args.quote,
                                getattr(args, "volume_unit", None), getattr(args, "interval_seconds", 60))
        if args.command == "preflight":
            arguments = args.credit_arguments
            return preflight(config, arguments[1:] if arguments[:1] == ["--"] else arguments)
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        report = run_observer(config, fetch_public=args.fetch_public, once=args.once,
                              max_cycles=args.max_cycles, stop=stop)
        return 0 if stop.is_set() else int(report["status"] == "OBSERVATION_DEGRADED")
    except Exception as error:
        print(canonical_json(_safe("RUNTIME_STOPPED_FAIL_CLOSED", failure=_failure(error))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
