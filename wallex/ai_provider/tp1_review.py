"""Opt-in TP-1 research veto, actual Strategy evidence; never trading permission."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import getpass
from hashlib import sha256
from pathlib import Path
import re
import json
import sqlite3
from contextlib import closing
import time
import warnings

from wallex.market.evaluation import EvidenceKind, canonical_json
from .client import BoxAPIClient, UrllibTransport

_FIELDS = {"mode", "symbol", "as_of", "evidence_hash", "strategy_version", "evidence_kind",
           "source_commit", "last_close", "ema20_h1", "ema50_h1", "ema20_h1_minus3",
           "ema20_m15", "atr14_m15", "distance_m15", "previous_close_m15", "previous_ema20_m15", "risk", "credit"}
_NUMBERS = {"last_close", "ema20_h1", "ema50_h1", "ema20_h1_minus3", "ema20_m15", "atr14_m15", "distance_m15", "previous_close_m15", "previous_ema20_m15"}


def valid_context(value):
    observation = type(value) is dict and value.get("mode") == "TP1_OBSERVATION_RESEARCH_ONLY"
    fields = _FIELDS | {"technical_eligible", "trend_eligible", "pullback_eligible"} if observation else _FIELDS
    if type(value) is not dict or set(value) != fields:
        return False
    try:
        return (value["mode"] in {"TP1_TECHNICAL_RESEARCH_ONLY", "TP1_OBSERVATION_RESEARCH_ONLY"}
            and (not observation or all(type(value[k]) is bool for k in ("technical_eligible", "trend_eligible", "pullback_eligible")))
            and re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", value["symbol"]) is not None
            and type(value["as_of"]) is int and 0 <= value["as_of"] <= 253402300799
            and re.fullmatch(r"[0-9a-f]{64}", value["evidence_hash"]) is not None
            and re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) is not None
            and value["strategy_version"] == "TP1_H1_TREND_M15_PULLBACK_V1"
            and value["evidence_kind"] in {e.value for e in EvidenceKind}
            and value["risk"] == value["credit"] == "UNVERIFIED"
            and all(type(value[k]) is str and len(value[k]) <= 100
                    and Decimal(value[k]).is_finite()
                    and (True if observation and k == "distance_m15" else
                         Decimal(value[k]) >= 0 if observation and k == "atr14_m15" else Decimal(value[k]) > 0)
                    for k in _NUMBERS))
    except (ValueError, TypeError, ArithmeticError):
        return False


class CandidateAdviser:
    """One bounded call per unique candidate hash; caches replies within this run.

    Decimal evidence is sent as exact strings. AI REVIEW retains a research
    candidate; NO_TRADE vetoes it. Neither can change execution or Risk results.
    """
    def __init__(self, client, *, source_commit, evidence_kind, source_hashes, max_calls=1, clock=time.time, assess_observation=False):
        if (type(client) is not BoxAPIClient or type(evidence_kind) is not EvidenceKind
                or re.fullmatch(r"[0-9a-f]{40}", source_commit or "") is None
                or type(assess_observation) is not bool
                or type(max_calls) is not int or not 1 <= max_calls <= 5
                or set(source_hashes) != {"m1", "h1"}
                or any(re.fullmatch(r"[0-9a-f]{64}", v or "") is None for v in source_hashes.values())):
            raise ValueError("INVALID_ADVISER_CONFIGURATION")
        self.client, self.source_commit, self.evidence_kind = client, source_commit, evidence_kind
        self.source_hashes, self.max_calls, self.clock = dict(source_hashes), max_calls, clock
        self.assess_observation = assess_observation
        self.calls, self.records, self._cache = 0, [], {}

    def __call__(self, frame, assessment):
        closed = {"status": "NOT_A_TECHNICAL_CANDIDATE", "action": "NO_TRADE", "executable_ready": False}
        if not assessment.frame_valid or (not self.assess_observation and not assessment.technical_eligible) or assessment.metadata is None:
            return closed
        meta = assessment.metadata
        if frame.as_of != meta.as_of:
            return closed | {"status": "EVIDENCE_MISMATCH"}
        evidence = {"metadata": meta, "trend": assessment.trend, "pullback": assessment.pullback,
                    "sources": self.source_hashes, "source_commit": self.source_commit,
                    "evidence_kind": self.evidence_kind.value}
        digest = sha256(canonical_json(evidence).encode("ascii")).hexdigest()
        if digest in self._cache:
            return dict(self._cache[digest])
        context = {"mode": "TP1_OBSERVATION_RESEARCH_ONLY" if self.assess_observation else "TP1_TECHNICAL_RESEARCH_ONLY", "symbol": meta.symbol, "as_of": meta.as_of,
            "evidence_hash": digest, "source_commit": self.source_commit, "evidence_kind": self.evidence_kind.value,
            "strategy_version": "TP1_H1_TREND_M15_PULLBACK_V1", "risk": "UNVERIFIED",
            "credit": "UNVERIFIED", "last_close": str(assessment.pullback.current_close),
            "ema20_h1": str(assessment.trend.ema20), "ema50_h1": str(assessment.trend.ema50),
            "ema20_h1_minus3": str(assessment.trend.ema20_three_bars_ago),
            "previous_close_m15": str(assessment.pullback.previous_close),
            "previous_ema20_m15": str(assessment.pullback.previous_ema20),
            "ema20_m15": str(assessment.pullback.current_ema20), "atr14_m15": str(assessment.pullback.current_atr14),
            "distance_m15": str(assessment.pullback.distance_from_ema20)}
        if self.assess_observation:
            context.update(technical_eligible=assessment.technical_eligible,
                           trend_eligible=assessment.trend.eligible, pullback_eligible=assessment.pullback.eligible)
        if not valid_context(context):
            return closed | {"status": "INVALID_CONTEXT"}
        if self.calls >= self.max_calls:
            return closed | {"status": "CALL_BUDGET_EXHAUSTED", "evidence_hash": digest}
        requested_at = int(self.clock())
        self.calls += 1
        result = self.client.review_technical(context)
        record = {"status": result.status, "advisory_decision": result.decision,
            "retained_for_research": assessment.technical_eligible and result.status == "OK" and result.decision == "REVIEW",
            "assessment_mode": context["mode"], "technical_eligible": assessment.technical_eligible,
            "provider": result.provider, "endpoint": self.client.endpoint,
            "http_status": result.http_status, "response_sha256": result.response_sha256,
            "explanation": result.explanation,
            "reason_codes": list(result.reason_codes), "requested_model": result.requested_model,
            "response_model": result.response_model, "usage": None if result.usage is None else asdict(result.usage),
            "inference_source": "BOXAPI_HTTPS" if self.client.uses_https_transport else "SYNTHETIC_TEST_TRANSPORT",
            "evidence_kind": self.evidence_kind.value, "evidence_hash": digest, "source_hashes": self.source_hashes,
            "source_commit": self.source_commit, "strategy_version": context["strategy_version"],
            "as_of": meta.as_of, "requested_at": requested_at, "completed_at": int(self.clock()),
            "action": "NO_TRADE", "executable_ready": False}
        self._cache[digest] = record
        self.records.append(record)
        return dict(record)


def persist_review(state, report):
    """Read back the exact committed event via a separate read-only connection."""
    from wallex.audit.ledger import Ledger
    from wallex.infra.agent_runtime import PARTITION
    stamp = int(time.time())
    path = Path(state) / "audit.sqlite"
    event_id = Ledger(path).record_event("public_tp1_ai_review", account_id=PARTITION,
        run_id="ai-research-"+str(stamp), trace_id="public-only",
        at=datetime.fromtimestamp(stamp, timezone.utc), payload=report)
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        row = db.execute("SELECT payload_json FROM audit_events WHERE event_seq=? AND kind=? AND account_id=?",
                         (int(event_id.removeprefix("audit-")), "public_tp1_ai_review", PARTITION)).fetchone()
    if row is None or json.loads(row[0]) != json.loads(canonical_json(report)):
        raise ValueError("AUDIT_READBACK_FAILED")
    return {"audit_event_id": event_id, "audit_readback_verified": True,
            "audit_payload_sha256": sha256(row[0].encode("ascii")).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--review-ai", action="store_true", help="Explicit one-call BoxAPI opt-in; backend .env credentials")
    parser.add_argument("--assess-observation", action="store_true",
                        help="Explicit real-model research assessment even without a TP-1 candidate")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if not (args.review_ai or args.assess_observation):
        print(canonical_json({"status": "AI_OPT_IN_REQUIRED", "action": "NO_TRADE", "executable_ready": False}))
        return 2
    try:
        from wallex.audit.ledger import Ledger
        from wallex.infra.agent_runtime import PARTITION
        from wallex.infra.observer_api import snapshot
        from wallex.shadow.offline_tp1 import run_offline
        from wallex.shadow.release_evidence import read_public_source
        state = args.state_dir.resolve()
        public = snapshot(state)
        observed = public["last_observation"]
        if observed is None or public["data_status"] not in {"RECENT_CAPTURED", "STALE"}:
            raise ValueError("PUBLIC_EVIDENCE_UNAVAILABLE")
        files = list((state/"observations").glob("*/report.json"))
        if len(files) > 2048:
            raise ValueError("SOURCE_SEARCH_LIMIT")
        selected = None
        for file in sorted(files, reverse=True):
            try:
                raw, projected, ranges = read_public_source(file.parent, symbol=observed["symbol"], as_of=observed["decision_cutoff"])
                if all(projected["source_hashes"].get(k) == observed["source_hashes"].get(k) for k in ("m1", "h1")):
                    selected = file.parent
                    break
            except (ValueError, KeyError, OSError):
                continue
        if selected is None:
            raise ValueError("SOURCE_HASH_MISMATCH")
        binding = __import__("json").loads((state/"observer.json").read_text(encoding="utf-8"))
        settings = dict(symbol=observed["symbol"], base_asset=binding["base_asset"], quote_asset=binding["quote_asset"],
            m1_start=ranges["m1"][0], m1_end=ranges["m1"][1], h1_start=ranges["h1"][0], h1_end=ranges["h1"][1],
            as_of=observed["decision_cutoff"], evidence_kind=EvidenceKind(raw["pipeline"]["evidence_kind"]), volume_unit="base")
        baseline = run_offline(selected/"m1.json", selected/"h1.json", **settings)
        records, calls = [], 0
        if re.fullmatch(r"[0-9a-f]{40}", args.source_commit or "") is None:
            raise ValueError("SOURCE_COMMIT_REQUIRED")
        if args.assess_observation and settings["evidence_kind"] is not EvidenceKind.HISTORICAL_DATA:
            raise ValueError("GENUINE_HISTORICAL_OBSERVATIONS_REQUIRED")
        if args.assess_observation and not baseline["evaluated_steps"]:
            raise ValueError("NO_VALID_TECHNICAL_ASSESSMENT_AVAILABLE")
        if args.assess_observation or baseline["summary"]["technical_eligible_steps"]:
            from wallex.infra.config import load_settings
            settings_config=load_settings(args.env_file)
            adviser = CandidateAdviser(BoxAPIClient.from_settings(settings_config),
                source_commit=args.source_commit, evidence_kind=settings["evidence_kind"],
                source_hashes={k: observed["source_hashes"][k] for k in ("m1", "h1")},
                assess_observation=args.assess_observation)
            target = max(step["as_of"] for step in baseline["evaluated_steps"]) if args.assess_observation else None
            def review_step(frame, assessment):
                if target is not None and frame.as_of != target:
                    return {"status": "NOT_SELECTED_OBSERVATION", "action": "NO_TRADE", "executable_ready": False}
                return adviser(frame, assessment)
            run_offline(selected/"m1.json", selected/"h1.json", **settings, adviser=review_step)
            records, calls = adviser.records, adviser.calls
        report = {"status": "REVIEW_RECORDED" if records else "NO_TECHNICAL_CANDIDATE", "symbol": observed["symbol"],
            "decision_cutoff": settings["as_of"], "source_hashes": {k: observed["source_hashes"][k] for k in ("m1", "h1")},
            "provider_calls": calls, "records": records, "assessment_mode": "OBSERVATION" if args.assess_observation else "CANDIDATE",
            "observation_age_seconds": public["observation_age_seconds"],
            "technical_summary": baseline["summary"], "action": "NO_TRADE", "executable_ready": False}
        report["inference_succeeded"] = any(row["status"] == "OK" and row["http_status"] == 200
            and row["response_sha256"] is not None and row["inference_source"] == "BOXAPI_HTTPS" for row in records)
        if records and not report["inference_succeeded"]:
            report["status"] = "PROVIDER_INFERENCE_FAILED"
        if args.assess_observation and not records:
            report["status"] = "OBSERVATION_CONTEXT_UNUSABLE_NO_REQUEST"
        verified = persist_review(state, report)
        print(canonical_json(report | verified))
        return 0 if report["inference_succeeded"] else 1
    except (Exception, KeyboardInterrupt) as error:
        known = {"PUBLIC_EVIDENCE_UNAVAILABLE", "SOURCE_SEARCH_LIMIT", "SOURCE_HASH_MISMATCH", "SOURCE_COMMIT_REQUIRED",
                 "GENUINE_HISTORICAL_OBSERVATIONS_REQUIRED", "NO_VALID_TECHNICAL_ASSESSMENT_AVAILABLE", "AUDIT_READBACK_FAILED", "BOXAPI_API_KEY_MISSING", "BOXAPI_ORIGIN_OR_ROUTE_NOT_ALLOWED"}
        reason = str(error) if type(error) is ValueError and str(error) in known else "LOCAL_EVIDENCE_OR_PROMPT_ERROR"
        print(canonical_json({"status": "AI_REVIEW_FAILED_CLOSED", "reason": reason, "action": "NO_TRADE", "executable_ready": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
