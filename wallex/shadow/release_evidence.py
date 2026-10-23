"""Offline release evidence: existing TP-1 Shadow + fee-aware simulation, no orders."""

import argparse
from bisect import bisect_right
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from wallex.backtest.engine import SimulationConfig, run_backtest
from wallex.infra.observer_api import _decode, _observation
from wallex.market.evaluation import EvidenceKind, canonical_json
from wallex.market.gap_aware import analyze_minutes
from wallex.market.history import ENDPOINT, FIELDS, decode_hours
from wallex.market.normalization import Timeframe
from wallex.market.replay import ReplayPlan
from .comparison import compare_runs
from .offline_tp1 import _dataset, _read, run_offline
from .tp1 import run_tp1_shadow, tp1_decision

VERSION = "tp1-release-evidence-v1"


def read_public_source(observation_dir, *, symbol, as_of):
    """Validate the existing observer source packet before any downstream use."""
    source=Path(observation_dir)
    raw=_decode(_read(source/'report.json'))
    projected=_observation(raw,symbol)
    if raw.get('decision_cutoff') != as_of or raw.get('volume_unit') != 'base':
        raise ValueError("CUTOFF_MISMATCH_OR_VOLUME_ASSUMPTION_MISSING")
    evidence_kind=EvidenceKind(raw['pipeline']['evidence_kind'])
    ranges={}
    for name,resolution in (('m1','1'),('h1','60')):
        request=raw['requests'][name]
        url=urlsplit(request['url'])
        query=parse_qs(url.query,strict_parsing=True)
        if (url.scheme+'://'+url.netloc+url.path != ENDPOINT or url.fragment
                or set(query) != {'symbol','resolution','from','to'}
                or query['symbol'] != [symbol] or query['resolution'] != [resolution]
                or len(query['from']) != 1 or len(query['to']) != 1):
            raise ValueError("PUBLIC_SOURCE_REQUEST_MISMATCH")
        start,end=int(query['from'][0]),int(query['to'][0])+1
        expected_end=as_of if name=='m1' else as_of//3600*3600
        if not 0 <= start < end == expected_end:
            raise ValueError("PUBLIC_SOURCE_RANGE_MISMATCH")
        payload=_read(source/(name+'.json'))
        if sha256(payload).hexdigest() != request['raw_sha256'] or request['http_status'] != 200:
            raise ValueError("PUBLIC_SOURCE_HASH_OR_STATUS_MISMATCH")
        ranges[name]=(start,end,payload)
    return raw, projected, ranges


def build_evidence(observation_dir, output, *, symbol, base, quote, as_of, source_commit, simulation):
    """Hash-pin supplied public files; capital/costs are simulation assumptions.

    No Pe, account, equity, quote freshness or execution permission is invented.
    Existing TP-1 returns NO_TRADE: zero simulated fills is not profitability proof.
    """
    if re.fullmatch(r"[0-9a-f]{40}", source_commit or "") is None or type(simulation) is not SimulationConfig:
        raise ValueError("EXPLICIT_SOURCE_COMMIT_AND_SIMULATION_REQUIRED")
    if type(as_of) is not int or as_of < 0 or as_of % 900:
        raise ValueError("INVALID_EXPLICIT_CUTOFF")
    output = Path(output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[2]) or output.exists():
        raise ValueError("NEW_OUTPUT_OUTSIDE_REPOSITORY_REQUIRED")
    source=Path(observation_dir)
    raw,projected,ranges=read_public_source(source,symbol=symbol,as_of=as_of)
    evidence_kind=EvidenceKind(raw['pipeline']['evidence_kind'])
    m1_start,m1_end,m1=ranges['m1']
    h1_start,h1_end,h1=ranges['h1']
    offline=run_offline(source/'m1.json',source/'h1.json',symbol=symbol,base_asset=base,quote_asset=quote,
        m1_start=m1_start,m1_end=m1_end,h1_start=h1_start,h1_end=h1_end,as_of=as_of,
        evidence_kind=evidence_kind,volume_unit='base')
    gap=analyze_minutes(m1,symbol=symbol,base_asset=base,quote_asset=quote,start=m1_start,end=m1_end,as_of=as_of,volume_unit='base')
    hours=decode_hours(h1,symbol=symbol,start=h1_start,end=h1_end,as_of=as_of)
    output.mkdir(parents=True,exist_ok=False)
    source_out=output/'public-source'
    source_out.mkdir()
    (source_out/'m1.json').write_bytes(m1)
    (source_out/'h1.json').write_bytes(h1)
    descriptor=projected | {'volume_unit':'base',
        'pipeline':projected['pipeline'] | {'evidence_kind':evidence_kind.value},
        'requests':{name:{'url':raw['requests'][name]['url'],'http_status':200,
                          'raw_sha256':sha256(values[2]).hexdigest()} for name,values in ranges.items()}}
    descriptor['orderbook']=dict(projected['orderbook'])
    if 'depth' in projected['source_hashes']:
        descriptor['orderbook']['raw_sha256']=projected['source_hashes']['depth']
    (source_out/'report.json').write_text(canonical_json(descriptor)+'\n',encoding='utf-8')
    (output/'public-observer.json').write_text(canonical_json(projected)+'\n',encoding='utf-8')
    (output/'offline-tp1.json').write_text(canonical_json(offline)+'\n',encoding='utf-8')
    derived=output/'derived'
    derived.mkdir()
    h1_dataset=_dataset(({key:getattr(bar,key) for key in FIELDS} for bar in hours),derived/'h1.jsonl',
                        symbol=symbol,timeframe=Timeframe.H1,as_of=as_of)
    closes=[bar.timestamp+3600 for bar in hours]
    runs=[]
    for index,segment in enumerate(gap['segments']):
        if len(segment['candles']) < 21 or len(hours) < 250:
            continue  # Short segments remain explicit in offline-tp1.json; never join them.
        end=segment['candles'][-1]['timestamp']+900
        start=max(segment['candles'][20]['timestamp']+900,closes[249])
        if start > end:
            continue
        m15=_dataset(segment['candles'],derived/f'm15-{index}.jsonl',symbol=symbol,timeframe=Timeframe.M15,as_of=end)
        prefix=replace(h1_dataset,candles=h1_dataset.candles[:bisect_right(closes,end)],as_of=end)
        plan=ReplayPlan(m15,prefix,base,quote,start,end)
        settings=dict(strategy_version=VERSION,evidence_kind=evidence_kind)
        shadow=run_tp1_shadow(plan,**settings)
        backtest=run_backtest(plan,tp1_decision,config=simulation,**settings)
        comparison=compare_runs(shadow,backtest)
        if not comparison.matches or backtest.fills or any(record.decision is not None and record.decision.action.value != 'NO_TRADE' for record in shadow.evaluations):
            raise ValueError("NON_DIAGNOSTIC_OR_INCONSISTENT_RELEASE_RESULT")
        shadow.export(output/f'shadow-{index}')
        backtest.export(output/f'backtest-{index}')
        runs.append({'segment':index,'shadow_run_id':shadow.run_id,'backtest_run_id':backtest.run_id,
                     'comparison':comparison,'shadow':shadow.summary,'backtest':backtest.summary})
    manifest={'source_commit':source_commit,'mode':'OFFLINE_RELEASE_EVIDENCE','evidence_kind':evidence_kind.value,
        'symbol':symbol,'base_asset':base,'quote_asset':quote,'as_of':as_of,
        'simulation_assumptions':simulation,'volume_assumption':'CALLER_ATTESTED_BASE_NOT_VERIFIED',
        'technical_summary':offline['summary'],'runs':runs,
        'ai':{'actual_candidate_contribution':'NONE','provider_calls':0,'reason':'AI_REVIEW_NOT_REQUESTED_IN_THIS_OFFLINE_RUN'},
        'limitations':['NO_EXECUTABLE_TP1_ENTRY_OR_EXIT_POLICY','ZERO_FILLS_NOT_PROFITABILITY_EVIDENCE',
                      'SIMULATED_CAPITAL_NOT_ACCOUNT_EQUITY','FEES_SLIPPAGE_NOT_VERIFIED_ACCOUNT_COSTS'],
        'action':'NO_TRADE','executable_ready':False,
        'artifacts':{str(path.relative_to(output)).replace('\\','/'):sha256(path.read_bytes()).hexdigest()
                     for path in sorted(output.rglob('*')) if path.is_file()}}
    (output/'manifest.json').write_text(canonical_json(manifest)+'\n',encoding='utf-8')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('observation-dir','output','symbol','base','quote','source-commit'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--as-of',type=int,required=True,help='Explicit UTC Unix-second completed M15 cutoff')
    for name in ('simulation-cash','fee-rate','slippage-rate'):
        parser.add_argument('--'+name,type=Decimal,required=True,help='Explicit HYPOTHETICAL simulation assumption')
    parser.add_argument('--latency-seconds',type=int,required=True)
    args=parser.parse_args(argv)
    try:
        report=build_evidence(args.observation_dir,args.output,symbol=args.symbol,base=args.base,quote=args.quote,as_of=args.as_of,
            source_commit=args.source_commit,simulation=SimulationConfig(args.simulation_cash,args.fee_rate,args.slippage_rate,args.latency_seconds))
        print(canonical_json({'source_commit':report['source_commit'],'technical_summary':report['technical_summary'],
                              'segment_runs':len(report['runs']),'action':'NO_TRADE','executable_ready':False}))
        return 0
    except Exception:
        print('{"status":"RELEASE_EVIDENCE_FAILED_CLOSED","action":"NO_TRADE","executable_ready":false}')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
