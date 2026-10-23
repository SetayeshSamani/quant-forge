"""Offline composition of saved M1/native H1 UDF bytes and existing TP-1 Shadow."""

import argparse
from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from wallex.market.datasets import load_dataset
from wallex.market.evaluation import Action, EvidenceKind, canonical_json
from wallex.market.frames import MIN_H1_HISTORY, MIN_M15_HISTORY, MarketFrameError
from wallex.market.gap_aware import analyze_minutes
from wallex.market.history import FIELDS, MAX_BYTES, decode_hours
from wallex.market.normalization import Timeframe, TimestampUnit
from wallex.market.replay import ReplayPlan
from .tp1 import run_tp1_shadow


def _read(path):
    with Path(path).open("rb") as source:
        payload = source.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("INPUT_TOO_LARGE")
    return payload


def _dataset(rows, path, *, symbol, timeframe, as_of):
    # Hash actual derived JSONL bytes through the existing strict loader. The
    # original raw-response hashes remain separate; neither proves source identity.
    path.write_text("".join(canonical_json({key: row[key] for key in FIELDS}) + "\n"
                            for row in rows), encoding="utf-8", newline="\n")
    return load_dataset(path, symbol=symbol, timeframe=timeframe,
                        timestamp_unit=TimestampUnit.SECONDS, as_of=as_of)


def _decision(record):
    if record.error_code or record.decision is None:
        return {"technical_eligible": None, "blockers": [record.error_code or "NO_DECISION"]}
    decision = record.decision
    if decision.action is not Action.NO_TRADE or decision.quantity_base is not None:
        raise ValueError("NON_DIAGNOSTIC_SHADOW_RESULT")
    technical = json.loads(decision.reason)
    detail = json.loads(decision.diagnostics_json)
    blockers = ["frame:" + reason for reason in technical["reasons"]["frame"]
                if reason not in ("FRAME_VALIDATED", "TECHNICAL_CONDITIONS_MET")]
    for stage in ("trend", "pullback"):
        evidence = detail["technical_evidence"][stage]
        if evidence is not None and not evidence["eligible"]:
            blockers.extend(stage + ":" + reason for reason in evidence["reasons"])
    reasons = {key: detail[key] for key in ("input_reasons", "context_reasons", "risk_reasons",
                                           "credit_reasons", "loss_reasons", "portfolio_reasons", "sizing_reasons")}
    for stage, values in reasons.items():
        blockers.extend(stage + ":" + reason for reason in (values or []))
    return {"technical_eligible": technical["technical_eligible"],
            "technical_reasons": technical["reasons"],
            "technical_evidence": detail["technical_evidence"],
            **reasons, "blockers": sorted(set(blockers)),
            **({"ai": detail["ai"]} if "ai" in detail else {})}


def run_offline(m1_input, h1_input, *, symbol, base_asset, quote_asset,
                m1_start, m1_end, h1_start, h1_end, as_of,
                evidence_kind, volume_unit=None, adviser=None):
    """Read two raw public UDF responses; never fetch, infer Pe or supply accounts.

    Ranges are explicit UTC OPEN seconds, end exclusive; as_of closes both files.
    M1 gaps produce independent M15 histories. Each causal prefix is checked by
    ReplayPlan before the unchanged Shadow/Strategy/Risk diagnostics can run.
    Unknown volume units block model construction; optional base is only a caller
    assumption. No source authentication, market readiness or risk approval.
    """
    if type(evidence_kind) is not EvidenceKind:
        raise ValueError("EXPLICIT_EVIDENCE_KIND_REQUIRED")
    m1_bytes, h1_bytes = _read(m1_input), _read(h1_input)
    gap = analyze_minutes(m1_bytes, symbol=symbol, base_asset=base_asset, quote_asset=quote_asset,
                          start=m1_start, end=m1_end, as_of=as_of, volume_unit=volume_unit)
    hours = decode_hours(h1_bytes, symbol=symbol, start=h1_start, end=h1_end, as_of=as_of)
    h1_closes = [bar.timestamp + 3600 for bar in hours]
    counts = Counter(complete_m15_steps=gap["counts"]["complete_m15"], evaluated_steps=0,
                     insufficient_data_steps=0, blocked_without_evaluation=0,
                     replay_errors=0, technical_eligible_steps=0)
    blockers, segments, steps = Counter(), [], []
    if not gap["segments"]:
        blockers["NO_COMPLETE_M15_SEGMENTS"] += 1
    with TemporaryDirectory(prefix="quantforge-offline-tp1-") as directory:
        directory = Path(directory)
        h1 = None
        if volume_unit == "base":
            h1 = _dataset(({key: getattr(bar, key) for key in FIELDS} for bar in hours),
                          directory / "h1.jsonl", symbol=symbol, timeframe=Timeframe.H1, as_of=as_of)
        for index, segment in enumerate(gap["segments"]):
            row = {key: value for key, value in segment.items() if key != "candles"}
            row.update(m15_warmup_sufficient=segment["candle_count"] >= MIN_M15_HISTORY,
                       insufficient_data_steps=0, evaluated_steps=0, blocked_without_evaluation=0)
            local_blockers = Counter()
            m15 = None
            # Unknown volume semantics cannot be silently promoted to base volume.
            if volume_unit == "base":
                m15 = _dataset(segment["candles"], directory / f"m15-{index}.jsonl",
                               symbol=symbol, timeframe=Timeframe.M15, as_of=as_of)
                row["derived_dataset_sha256"] = m15.sha256
            for position, bar in enumerate(segment["candles"], start=1):
                cutoff = bar["timestamp"] + 900
                hourly_count = bisect_right(h1_closes, cutoff)
                missing = []
                if position < MIN_M15_HISTORY:
                    missing.append("INSUFFICIENT_CONSECUTIVE_M15_HISTORY")
                if hourly_count < MIN_H1_HISTORY:
                    missing.append("INSUFFICIENT_CONSECUTIVE_H1_HISTORY")
                if missing:
                    counts["insufficient_data_steps"] += 1
                    row["insufficient_data_steps"] += 1
                    local_blockers.update(missing)
                    continue
                if m15 is None:
                    counts["blocked_without_evaluation"] += 1
                    row["blocked_without_evaluation"] += 1
                    local_blockers["VOLUME_UNIT_UNVERIFIED"] += 1
                    continue
                try:
                    # Dataset hashes continue to identify their complete derived
                    # files, as in ReplayPlan.steps(); future bars are excluded.
                    plan = ReplayPlan(replace(m15, candles=m15.candles[:position], as_of=cutoff),
                                      replace(h1, candles=h1.candles[:hourly_count], as_of=cutoff),
                                      base_asset, quote_asset, cutoff, cutoff)
                except MarketFrameError as error:
                    counts["blocked_without_evaluation"] += 1
                    row["blocked_without_evaluation"] += 1
                    local_blockers[f"frame:{error.code.value}:{error.dataset}"] += 1
                    continue
                shadow = run_tp1_shadow(plan, strategy_version="tp1-offline-integration-v1",
                                        evidence_kind=evidence_kind, adviser=adviser)
                record = shadow.evaluations[0]
                result = _decision(record)
                counts["replay_errors"] += int(record.error_code is not None)
                counts["evaluated_steps"] += 1
                counts["technical_eligible_steps"] += int(result["technical_eligible"] is True)
                row["evaluated_steps"] += 1
                local_blockers.update(result["blockers"])
                steps.append({"as_of": cutoff, "segment_index": index, "run_id": shadow.run_id,
                              "m15_history": position, "h1_history": hourly_count, **result,
                              "action": "NO_TRADE", "executable_ready": False})
            row["blocking_reason_counts"] = dict(sorted(local_blockers.items()))
            blockers.update(local_blockers)
            segments.append(row)
    if adviser is not None:
        reviewed = [step["ai"] for step in steps if step.get("technical_eligible") and "ai" in step]
        counts["ai_reviewed_candidates"] = sum(row.get("status") == "OK" for row in reviewed)
        counts["ai_retained_candidates"] = sum(row.get("status") == "OK" and row.get("retained_for_research") is True for row in reviewed)
        counts["ai_vetoed_candidates"] = sum(row.get("status") == "OK" and row.get("advisory_decision") == "NO_TRADE" for row in reviewed)
        counts["ai_unavailable_candidates"] = sum(row.get("status") != "OK" for row in reviewed)
    return {
        "mode": "OFFLINE_TP1_INTEGRATION", "evidence_kind": evidence_kind.value,
        "instrument": gap["instrument"], "as_of": as_of, "timestamp_semantics": "UTC_OPEN_SECONDS",
        "ranges": {"m1": [m1_start, m1_end], "native_h1": [h1_start, h1_end]},
        "provenance": {"m1": gap["source"],
                       "native_h1": {"sha256": sha256(h1_bytes).hexdigest(), "byte_count": len(h1_bytes),
                                     "expected_timeframe": "H1", "spacing_seconds": 3600,
                                     "resolution_claim": "CALLER_SUPPLIED_NATIVE_60",
                                     "source_identity": "UNVERIFIED"},
                       "derived_h1_sha256": None if h1 is None else h1.sha256},
        "validation": {"m1_m15": gap["counts"], "h1_candles": len(hours),
                       "missing_m1_timestamps": gap["missing_m1_timestamps"],
                       "incomplete_m15_buckets": gap["incomplete_buckets"], "warnings": gap["warnings"]},
        "unverified": {key: "UNVERIFIED" for key in (
            "account_identity", "balances", "risk_equity", "credit_evidence", "fees", "pe",
            "quote_freshness", "depth", "execution_permissions", "provider_volume_units")},
        "volume_assumption": "CALLER_ATTESTED_BASE" if volume_unit else "UNVERIFIED",
        "requirements": {"consecutive_m15": MIN_M15_HISTORY, "consecutive_h1": MIN_H1_HISTORY,
                         "segments_may_be_combined": False},
        "summary": dict(counts), "blocking_reason_counts": dict(sorted(blockers.items())),
        "segments": segments, "evaluated_steps": steps,
        "action": "NO_TRADE", "executable_ready": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", action="store_true", required=True, help="Local files only; never network")
    for name in ("m1-input", "h1-input", "symbol", "base", "quote"):
        parser.add_argument("--" + name, required=True)
    for name in ("m1-start", "m1-end", "h1-start", "h1-end", "as-of"):
        parser.add_argument("--" + name, required=True, type=int, help="UTC Unix seconds; end exclusive")
    parser.add_argument("--evidence-kind", required=True, choices=[item.value for item in EvidenceKind])
    parser.add_argument("--volume-unit", choices=("base",), help="Explicit assumption, NOT verified units")
    args = parser.parse_args(argv)
    try:
        report = run_offline(args.m1_input, args.h1_input, symbol=args.symbol, base_asset=args.base,
                             quote_asset=args.quote, m1_start=args.m1_start, m1_end=args.m1_end,
                             h1_start=args.h1_start, h1_end=args.h1_end, as_of=args.as_of,
                             evidence_kind=EvidenceKind(args.evidence_kind), volume_unit=args.volume_unit)
    except (ValueError, OSError) as error:
        print(canonical_json({"status": "INPUT_REJECTED", "blockers": [str(error)],
                              "action": "NO_TRADE", "executable_ready": False}))
        return 1
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
