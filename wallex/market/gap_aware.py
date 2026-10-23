"""Opt-in offline M1 diagnostics, never a validated dataset or trading approval.

All present bars (including zero volume) retain recorded prices. Units and source
identity are unverified unless explicitly stated; carry-forward semantics are not
assumed. Existing strict ingestion/normalizers are unchanged.
"""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
import re

from .datasets import _unique_object
from .history import FIELDS, MAX_BYTES, _reject_constant, aggregate_minutes
from .evaluation import canonical_json
from .normalization import Timeframe, TimestampUnit, normalize_candles


def analyze_minutes(payload, *, symbol, base_asset, quote_asset, start, end, as_of, volume_unit=None):
    """Analyze [start, end) UTC minute opens; require an explicit closed cutoff.

    Whole-range gaps are reported, not accepted by strict ingestion. Individual
    contiguous source runs use its unchanged numeric/closed-candle normalizer.
    Normalizer reuse performs no unit verification: returned candle dictionaries
    are provisional research records with explicit source-volume semantics.
    """
    labels = (symbol, base_asset, quote_asset)
    if (any(type(value) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", value) is None for value in labels)
            or base_asset == quote_asset or symbol != base_asset + quote_asset):
        raise ValueError("INVALID_INSTRUMENT_IDENTITY")
    if (any(type(value) is not int for value in (start, end, as_of))
            or not 0 <= start < end <= as_of or start % 60 or end % 60 or end - start > 14 * 86400):
        raise ValueError("INVALID_RANGE_OR_CUTOFF")
    if volume_unit not in (None, "base"):
        raise ValueError("INVALID_VOLUME_ATTESTATION")
    if type(payload) is not bytes or len(payload) > MAX_BYTES:
        raise ValueError("INVALID_RESPONSE_SIZE")
    data = json.loads(payload.decode("utf-8"), parse_float=Decimal,
                      parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    keys = ("t", "o", "h", "l", "c", "v")
    if type(data) is not dict or data.get("s") != "ok" or any(type(data.get(key)) is not list for key in keys):
        raise ValueError("UDF_ARRAYS_REQUIRED")
    count = len(data["t"])
    if any(len(data[key]) != count for key in keys):
        raise ValueError("UDF_ARRAY_LENGTH_MISMATCH")
    if "symbol" in data and data["symbol"] != symbol:
        raise ValueError("SYMBOL_MISMATCH")
    if "completed" in data and (type(data["completed"]) is not list
            or len(data["completed"]) != count or any(value is not True for value in data["completed"])):
        raise ValueError("INCOMPLETE_SOURCE_CANDLE")
    runs = []
    previous = None
    for values in zip(*(data[key] for key in keys)):
        stamp = values[0]
        if type(stamp) is not int or not start <= stamp < end or stamp % 60:
            raise ValueError("INVALID_SOURCE_TIMESTAMP")
        if previous is not None and stamp <= previous:
            raise ValueError("DUPLICATE_OR_UNORDERED_TIMESTAMP")
        if previous is None or stamp != previous + 60:
            runs.append([])
        runs[-1].append(dict(zip(FIELDS, values + (True,))))
        previous = stamp
    # Validate every present record, including records in buckets that cannot emit.
    minutes = []
    for run in runs:
        minutes.extend(normalize_candles(run, symbol=symbol, timeframe=Timeframe.M1,
                                        timestamp_unit=TimestampUnit.SECONDS, as_of=as_of))
    for candle in minutes:
        for name in ("open", "high", "low", "close", "volume"):
            parts = getattr(candle, name).as_tuple()
            if len(parts.digits) > 100 or abs(parts.exponent) > 100:
                raise ValueError("NUMERIC_RESOURCE_LIMIT")
    by_time = {candle.timestamp: candle for candle in minutes}
    missing = [stamp for stamp in range(start, end, 60) if stamp not in by_time]
    zero_volume = [candle.timestamp for candle in minutes if candle.volume == 0]
    units = "CALLER_ATTESTED_BASE" if volume_unit == "base" else "UNVERIFIED"
    incomplete, segments = [], []
    last_complete = None
    for opened in range(start - start % 900, end, 900):
        expected = list(range(opened, opened + 900, 60))
        absent = [stamp for stamp in expected if stamp not in by_time]
        if absent:
            incomplete.append({"open_at": opened, "close_at": opened + 900,
                               "missing_timestamps": absent,
                               "missing_in_expected_range": [stamp for stamp in absent if start <= stamp < end],
                               "outside_expected_range": [stamp for stamp in absent if not start <= stamp < end]})
            continue
        candle = aggregate_minutes(tuple(by_time[stamp] for stamp in expected), symbol=symbol,
                                   timeframe=Timeframe.M15, as_of=as_of)[0]
        if last_complete is None or opened != last_complete + 900:
            segments.append({"start_open_at": opened, "end_close_at": opened + 900, "candles": []})
        segments[-1]["candles"].append({**{field: getattr(candle, field) for field in FIELDS},
                                       "volume_semantics": units})
        segments[-1]["end_close_at"] = opened + 900
        last_complete = opened
    for segment in segments:
        segment["candle_count"] = len(segment["candles"])
    complete_count = sum(segment["candle_count"] for segment in segments)
    warnings = ["RESEARCH_ONLY_NOT_A_VALIDATED_DATASET", "SOURCE_IDENTITY_NOT_AUTHENTICATED",
                "VOLUME_UNIT_CALLER_ATTESTED_NOT_VERIFIED" if volume_unit else "VOLUME_UNIT_UNVERIFIED"]
    if zero_volume:
        warnings.append("ZERO_VOLUME_PRICE_SEMANTICS_UNVERIFIED")
    if incomplete:
        warnings.append("INCOMPLETE_M15_BUCKETS")
    return {
        "mode": "OFFLINE_GAP_ANALYSIS", "status": "PARTIAL" if incomplete else "COVERAGE_COMPLETE_RESEARCH_ONLY",
        "fully_validated": False, "executable_action": "NO_TRADE", "executable_ready": False,
        "instrument": {"symbol": symbol, "base_asset": base_asset, "quote_asset": quote_asset},
        "source": {"kind": "CALLER_SUPPLIED_UDF_BYTES", "sha256": sha256(payload).hexdigest(),
                   "byte_count": len(payload), "expected_timeframe": "M1", "timestamp_semantics": "UTC_OPEN_SECONDS",
                   "volume_semantics": units, "volume_unit_verified": False,
                   "price_policy": "AS_RECORDED_ALL_PRESENT_MINUTES"},
        "range": {"start_inclusive": start, "end_exclusive": end, "as_of": as_of},
        "counts": {"expected_m1": (end - start) // 60, "observed_m1": count, "missing_m1": len(missing),
                   "zero_volume_m1": len(zero_volume), "complete_m15": complete_count,
                   "incomplete_m15": len(incomplete), "segments": len(segments)},
        "missing_m1_timestamps": missing, "zero_volume_m1_timestamps": zero_volume,
        "incomplete_buckets": incomplete, "segments": segments, "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", action="store_true", required=True, help="Opt in to local-file analysis; no network")
    parser.add_argument("--input", required=True, help="Saved raw UDF JSON response")
    for name in ("symbol", "base", "quote"):
        parser.add_argument("--" + name, required=True)
    for name in ("start", "end", "as-of"):
        parser.add_argument("--" + name, type=int, required=True, help="UTC Unix seconds; start/end minute-aligned")
    parser.add_argument("--volume-unit", choices=("base",), help="Optional caller attestation only; default UNVERIFIED")
    parser.add_argument("--summary-only", action="store_true", help="Omit candle bodies, retain segment bounds/counts")
    args = parser.parse_args(argv)
    try:
        with open(args.input, "rb") as source:
            payload = source.read(MAX_BYTES + 1)
        report = analyze_minutes(payload, symbol=args.symbol, base_asset=args.base, quote_asset=args.quote,
                                 start=args.start, end=args.end, as_of=args.as_of, volume_unit=args.volume_unit)
        if args.summary_only:
            report["segments"] = [{key: value for key, value in segment.items() if key != "candles"}
                                  for segment in report["segments"]]
        print(canonical_json(report))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
