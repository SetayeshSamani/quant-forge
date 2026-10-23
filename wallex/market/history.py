"""Opt-in public UDF history; strict M1 aggregation or independent native H1.

No import-time HTTP, credentials, account endpoints or write requests. Wire t is
accepted only as UTC open seconds; v requires caller-attested base volume. Actual
provider volume units remain independently unverified; an assumption is not verification.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, Inexact, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .datasets import _unique_object, load_dataset
from .evaluation import EvidenceKind, canonical_json
from .normalization import Timeframe, TimestampUnit, normalize_candles

ENDPOINT = "https://api.wallex.ir/v1/udf/history"
MAX_BYTES = 1048576
FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "completed")


@dataclass(frozen=True)
class HistoryRequest:
    symbol: str
    base_asset: str
    quote_asset: str
    start: int  # Inclusive UTC candle OPEN seconds, whole hour.
    end: int  # Exclusive UTC seconds; final candle closes here.
    output: Path
    volume_unit: str  # Explicit caller attestation; only base units are supported.
    chunk_hours: int = 4
    max_requests: int = 96
    timeout_seconds: int = 10


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_get(url, *, timeout_seconds):
    if not url.startswith(ENDPOINT + "?"):
        raise ValueError("PUBLIC_ENDPOINT_ONLY")
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity"}, method="GET")
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise ValueError("PUBLIC_HTTP_STATUS")
            payload = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise ValueError("PUBLIC_HTTP_" + str(status)) from None
    if len(payload) > MAX_BYTES:
        raise ValueError("RESPONSE_TOO_LARGE")
    return payload


def _reject_constant(value):
    raise ValueError("NONFINITE_JSON")


def decode_minutes(payload, *, symbol, start, end):
    return _decode_candles(payload, symbol=symbol, start=start, end=end, timeframe=Timeframe.M1)


def decode_hours(payload, *, symbol, start, end, as_of):
    """Validate native H1 opens in [start, end), closed by explicit UTC as_of.

    Actual timestamps, not the requested resolution, establish hourly spacing.
    No sorting, truncation, gap filling, aggregation or volume conversion occurs.
    """
    if (any(type(value) is not int for value in (start, end, as_of))
            or not 0 <= start < end <= as_of or start % 3600 or end % 3600
            or end - start > 14 * 86400):
        raise ValueError("INVALID_NATIVE_H1_RANGE_OR_CUTOFF")
    return _decode_candles(payload, symbol=symbol, start=start, end=end, timeframe=Timeframe.H1)


def _decode_candles(payload, *, symbol, start, end, timeframe):
    if type(payload) is not bytes or len(payload) > MAX_BYTES:
        raise ValueError("INVALID_RESPONSE_SIZE")
    data = json.loads(payload.decode("utf-8"), parse_float=Decimal,
                      parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    if type(data) is not dict or data.get("s") != "ok":
        raise ValueError("UDF_NO_COMPLETE_DATA")
    keys = ("t", "o", "h", "l", "c", "v")
    if any(type(data.get(key)) is not list for key in keys):
        raise ValueError("UDF_ARRAYS_REQUIRED")
    if (any(len(data[key]) != (end - start) // timeframe.value for key in keys)
            or any(type(stamp) is not int for stamp in data["t"])
            or data["t"] != list(range(start, end, timeframe.value))):
        raise ValueError(f"{timeframe.name}_COVERAGE_OR_SPACING_INVALID")
    if "symbol" in data and data["symbol"] != symbol:
        raise ValueError("SYMBOL_MISMATCH")
    if "completed" in data and (type(data["completed"]) is not list
            or len(data["completed"]) != len(data["t"]) or any(value is not True for value in data["completed"])):
        raise ValueError("INCOMPLETE_SOURCE_CANDLE")
    rows = [dict(zip(FIELDS, values + (True,))) for values in zip(*(data[key] for key in keys))]
    candles = normalize_candles(rows, symbol=symbol, timeframe=timeframe,
                                timestamp_unit=TimestampUnit.SECONDS, as_of=end)
    for candle in candles:
        for field in ("open", "high", "low", "close", "volume"):
            parts = getattr(candle, field).as_tuple()
            if len(parts.digits) > 100 or abs(parts.exponent) > 100:
                raise ValueError("NUMERIC_RESOURCE_LIMIT")
    return candles


def aggregate_minutes(candles, *, symbol, timeframe, as_of):
    if timeframe not in (Timeframe.M15, Timeframe.H1):
        raise ValueError("AGGREGATE_TIMEFRAME_INVALID")
    if any(c.timeframe is not Timeframe.M1 or c.symbol != symbol for c in candles):
        raise ValueError("M1_SOURCE_IDENTITY_REQUIRED")
    candles = normalize_candles([{field: getattr(c, field) for field in FIELDS} for c in candles],
                                symbol=symbol, timeframe=Timeframe.M1,
                                timestamp_unit=TimestampUnit.SECONDS, as_of=as_of)
    width = timeframe.value // 60
    if not candles or len(candles) % width or candles[0].timestamp % timeframe.value:
        raise ValueError("INCOMPLETE_AGGREGATION_BUCKET")
    rows = []
    with localcontext(Context(prec=400)) as context:
        context.traps[Inexact] = True  # Exact bounded-volume sums; caller context cannot round.
        for index in range(0, len(candles), width):
            group = candles[index:index + width]
            rows.append(dict(timestamp=group[0].timestamp, open=group[0].open,
                             high=max(c.high for c in group), low=min(c.low for c in group),
                             close=group[-1].close, volume=sum((c.volume for c in group), Decimal(0)),
                             completed=True))
    return normalize_candles(rows, symbol=symbol, timeframe=timeframe,
                             timestamp_unit=TimestampUnit.SECONDS, as_of=as_of)


def _validate_request(config, now):
    if type(config) is not HistoryRequest or config.volume_unit != "base":
        raise ValueError("EXPLICIT_BASE_VOLUME_ATTESTATION_REQUIRED")
    labels = (config.symbol, config.base_asset, config.quote_asset)
    if (any(type(value) is not str or re.fullmatch(r"[A-Z0-9]{1,30}", value) is None for value in labels)
            or config.base_asset == config.quote_asset or config.symbol != config.base_asset + config.quote_asset):
        raise ValueError("INVALID_SYMBOL_ASSETS")
    if (any(type(value) is not int for value in (config.start, config.end, now))
            or not 0 <= config.start < config.end <= now
            or config.start % 3600 or config.end % 3600 or config.end - config.start > 14 * 86400):
        raise ValueError("INVALID_OR_FUTURE_RANGE_MAX_14_DAYS")
    for value, limit in ((config.chunk_hours, 10), (config.max_requests, 128), (config.timeout_seconds, 30)):
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError("INVALID_RESOURCE_LIMIT")
    if (config.end - config.start + config.chunk_hours * 3600 - 1) // (config.chunk_hours * 3600) > config.max_requests:
        raise ValueError("REQUEST_BUDGET_EXCEEDED")
    output = Path(config.output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[2]) or output.exists():
        raise ValueError("OUTPUT_MUST_BE_NEW_AND_OUTSIDE_REPOSITORY")
    return output


def collect_history(config, *, fetch=False, transport=public_get,
                    clock=lambda: int(time.time()), evidence_kind=EvidenceKind.HISTORICAL_DATA, native_h1=False):
    if fetch is not True:
        raise ValueError("EXPLICIT_FETCH_REQUIRED")
    if type(evidence_kind) is not EvidenceKind:
        raise ValueError("EXPLICIT_EVIDENCE_KIND_REQUIRED")
    if type(native_h1) is not bool:
        raise ValueError("EXPLICIT_SOURCE_MODE_REQUIRED")
    resolution, spacing = ("60", 3600) if native_h1 else ("1", 60)
    observed = clock()
    output = _validate_request(config, observed)
    output.mkdir(parents=True, exist_ok=False)
    (output / "raw").mkdir()
    manifest = {"status": "INCOMPLETE", "endpoint": ENDPOINT, "symbol": config.symbol,
                "base_asset": config.base_asset, "quote_asset": config.quote_asset,
                "start": config.start, "end": config.end, "evidence_kind": evidence_kind.value,
                "source_resolution_requested": resolution, "source_spacing_seconds": spacing,
                "timestamp_semantics": "UTC_OPEN_SECONDS", "volume_semantics": "CALLER_ATTESTED_BASE",
                "aggregation": "NONE" if native_h1 else "COMPLETE_UTC_M1_TO_M15_AND_H1", "requests": [], "datasets": {}}
    if native_h1:
        manifest.update(ingestion="NATIVE_H1", volume_unit_verification="UNVERIFIED")
    source_candles = []
    try:
        for index, start in enumerate(range(config.start, config.end, config.chunk_hours * 3600)):
            end = min(start + config.chunk_hours * 3600, config.end)
            url = ENDPOINT + "?" + urlencode(dict(symbol=config.symbol, resolution=resolution, **{"from": start, "to": end - 1}))
            payload = transport(url, timeout_seconds=config.timeout_seconds)
            received = clock()
            if type(received) is not int or received < observed:
                raise ValueError("INVALID_OBSERVATION_TIME")
            observed = received
            if type(payload) is not bytes or len(payload) > MAX_BYTES:
                raise ValueError("INVALID_RESPONSE_SIZE")
            name = f"raw/{index:04d}.json"
            (output / name).write_bytes(payload)
            manifest["requests"].append(dict(file=name, url=url, observed_at=received,
                                             sha256=sha256(payload).hexdigest(), byte_count=len(payload)))
            if native_h1:
                source_candles.extend(decode_hours(payload, symbol=config.symbol, start=start, end=end, as_of=received))
            else:
                source_candles.extend(decode_minutes(payload, symbol=config.symbol, start=start, end=end))
        outputs = ((Timeframe.H1, "h1.jsonl"),) if native_h1 else ((Timeframe.M15, "m15.jsonl"), (Timeframe.H1, "h1.jsonl"))
        for timeframe, name in outputs:
            candles = source_candles if native_h1 else aggregate_minutes(source_candles, symbol=config.symbol, timeframe=timeframe, as_of=config.end)
            text = "".join(canonical_json({field: getattr(candle, field) for field in FIELDS}) + "\n" for candle in candles)
            (output / name).write_text(text, encoding="utf-8", newline="\n")
            dataset = load_dataset(output / name, symbol=config.symbol, timeframe=timeframe,
                                   timestamp_unit=TimestampUnit.SECONDS, as_of=config.end)
            manifest["datasets"][name] = dict(sha256=dataset.sha256, count=dataset.candle_count)
        manifest["status"] = "VALIDATED"
    finally:
        (output / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    return manifest


def research_history(directory, *, decision_start=None, decision_end=None, pe=None):
    from .replay import ReplayPlan
    from wallex.shadow.tp1 import run_tp1_shadow
    from wallex.shadow.research import evaluate_candidates

    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if (manifest["status"] != "VALIDATED" or manifest["endpoint"] != ENDPOINT
            or manifest["timestamp_semantics"] != "UTC_OPEN_SECONDS"
            or manifest["volume_semantics"] != "CALLER_ATTESTED_BASE"
            or manifest["source_resolution_requested"] != "1" or manifest["source_spacing_seconds"] != 60
            or not 1 <= len(manifest["requests"]) <= 128
            or manifest["symbol"] != manifest["base_asset"] + manifest["quote_asset"]):
        raise ValueError("VALIDATED_PUBLIC_HISTORY_REQUIRED")
    cursor = manifest["start"]
    for source in manifest["requests"]:
        url = urlsplit(source["url"])
        query = parse_qs(url.query, strict_parsing=True)
        if (url.scheme != "https" or url.netloc != "api.wallex.ir" or url.path != "/v1/udf/history"
                or url.fragment or set(query) != {"symbol", "resolution", "from", "to"}
                or any(len(values) != 1 for values in query.values())
                or query["symbol"] != [manifest["symbol"]] or query["resolution"] != ["1"]
                or int(query["from"][0]) != cursor):
            raise ValueError("SOURCE_REQUEST_IDENTITY_MISMATCH")
        previous = cursor
        cursor = int(query["to"][0]) + 1
        if (not previous < cursor <= manifest["end"] or cursor % 3600 or cursor - previous > 10 * 3600
                or type(source["observed_at"]) is not int or source["observed_at"] < manifest["end"]):
            raise ValueError("SOURCE_RANGE_OR_OBSERVATION_INVALID")
        if re.fullmatch(r"raw/[0-9]{4}\.json", source["file"]) is None:
            raise ValueError("INVALID_RAW_SOURCE_PATH")
        if sha256((directory / source["file"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("RAW_SOURCE_HASH_MISMATCH")
    if cursor != manifest["end"]:
        raise ValueError("INCOMPLETE_SOURCE_COVERAGE")
    datasets = []
    for timeframe, name in ((Timeframe.M15, "m15.jsonl"), (Timeframe.H1, "h1.jsonl")):
        dataset = load_dataset(directory / name, symbol=manifest["symbol"], timeframe=timeframe,
                               timestamp_unit=TimestampUnit.SECONDS, as_of=manifest["end"])
        if dataset.sha256 != manifest["datasets"][name]["sha256"] or dataset.candle_count != manifest["datasets"][name]["count"]:
            raise ValueError("DATASET_HASH_OR_COUNT_MISMATCH")
        datasets.append(dataset)
    start = manifest["start"] + 250 * 3600 if decision_start is None else decision_start
    end = manifest["end"] if decision_end is None else decision_end
    if not manifest["start"] + 250 * 3600 <= start <= end <= manifest["end"]:
        raise ValueError("TP1_RESEARCH_WINDOW_REQUIRES_250_H1_WARMUP")
    plan = ReplayPlan(*datasets, manifest["base_asset"], manifest["quote_asset"], start, end)
    report = evaluate_candidates(run_tp1_shadow(plan, pe=pe, strategy_version="tp1-public-history-research-v1",
                                  evidence_kind=EvidenceKind(manifest["evidence_kind"])))
    report["dataset_validation"] = {"datasets": manifest["datasets"], "raw_responses": len(manifest["requests"]),
                                     "source_spacing_seconds": 60, "volume_semantics": manifest["volume_semantics"]}
    return report


def utc_hour(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(None) or stamp.microsecond:
        raise argparse.ArgumentTypeError("Explicit UTC whole-hour timestamp required")
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = delta.days * 86400 + delta.seconds
    if seconds < 0 or seconds % 3600:
        raise argparse.ArgumentTypeError("Explicit UTC whole-hour timestamp required")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--research", action="store_true")
    for field in ("symbol", "base", "quote", "output", "input"):
        parser.add_argument("--" + field)
    for field in ("start", "end", "decision-start", "decision-end"):
        parser.add_argument("--" + field, type=utc_hour)
    parser.add_argument("--volume-unit", choices=("base",), help="Attest provider v is base volume; never guessed/converted")
    parser.add_argument("--native-h1", action="store_true", help="Fetch native resolution=60 H1 only; no M15 or research output")
    parser.add_argument("--chunk-hours", type=int, default=4)
    parser.add_argument("--max-requests", type=int, default=96)
    parser.add_argument("--pe", type=Decimal, help="Explicit provisional maximum BUY price; never inferred")
    args = parser.parse_args(argv)
    if args.native_h1 and not args.fetch:
        parser.error("--native-h1 requires --fetch")
    try:
        if args.fetch:
            if any(getattr(args, key) is None for key in ("symbol", "base", "quote", "start", "end", "output", "volume_unit")):
                parser.error("fetch requires symbol/base/quote/start/end/output/volume-unit")
            result = collect_history(HistoryRequest(args.symbol, args.base, args.quote, args.start, args.end,
                                     Path(args.output), args.volume_unit, args.chunk_hours, args.max_requests), fetch=True, native_h1=args.native_h1)
        else:
            if args.input is None:
                parser.error("research requires input directory")
            result = research_history(args.input, decision_start=args.decision_start, decision_end=args.decision_end, pe=args.pe)
        print(canonical_json(result))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
