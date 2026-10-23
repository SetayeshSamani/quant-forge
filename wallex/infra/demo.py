"""Local no-order demo supervisor. Explicit start opts into public/readonly/AI.

No HTTPS order gateway is activated. Only the explicitly synthetic HTTP_MOCK
gateway runs for the simulation. Configuration flags cannot enable real writes.
Control files contain only local process metadata, never API keys or account IDs.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler
import uuid

from .config import load_settings, ConfigurationError, ROOT
from wallex.market.evaluation import canonical_json

VERSION="quantforge-demo-control-v1"


def _paths(settings):
    return settings.data_dir/'demo-control',settings.data_dir/'observer'


def _running(control):
    try:
        opener=build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:'+str(control['port'])+'/api/health',timeout=1) as reply:
            return json.load(reply).get('demo_instance')==control['instance']
    except Exception:return False


def _control(path):
    if path.stat().st_size>4096:raise ValueError()
    value=json.loads(path.read_text())
    if (value.get('version')!=VERSION or re.fullmatch(r'[0-9a-f]{32}',value.get('instance','')) is None
            or re.fullmatch(r'[0-9a-f]{32}',value.get('token','')) is None
            or type(value.get('port')) is not int or not 1024<=value['port']<=65535):raise ValueError()
    return value


def describe(settings):
    directory,_=_paths(settings);path=directory/'control.json'
    active=False
    if path.exists():
        try:active=_running(_control(path))
        except Exception:pass
    return {'data_dir':str(settings.data_dir),'port':settings.backend_port,'python':sys.executable,
        'url':'http://127.0.0.1:'+str(settings.backend_port),'running':active,'mode':settings.trading_mode,
        'action':'NO_TRADE','executable_ready':False}


def stop_demo(settings):
    directory,_=_paths(settings);path=directory/'control.json'
    if not path.exists():return {'status':'ALREADY_STOPPED','action':'NO_TRADE'}
    control=_control(path)
    if not _running(control):return {'status':'NO_RUNNING_MANAGED_DEMO','action':'NO_TRADE'}
    request=directory/('stop-'+control['instance']+'.json')
    request.write_text(canonical_json({'instance':control['instance'],'token':control['token']}))
    end=time.monotonic()+50
    while time.monotonic()<end:
        if not path.exists():return {'status':'STOPPED_DATA_PRESERVED','action':'NO_TRADE'}
        time.sleep(.25)
    return {'status':'STOP_PENDING_NO_FORCE_KILL','action':'NO_TRADE'}


def _diagnostics(settings,state,stop):
    from wallex.audit.ledger import Ledger
    from .agent_runtime import PARTITION
    from .spot_preflight import check_configured_account
    from .observer_api import snapshot
    until=time.monotonic()+30
    while not (state/'audit.sqlite').exists():
        if stop.wait(.25) or time.monotonic()>until:return
    def publish(report=None,venue=None):
        ok=report is not None and report.get('authenticated_reads_succeeded') is True
        payload={'mode':settings.trading_mode,'account':'PENDING' if report is None else 'AUTHENTICATED_READS_VERIFIED' if ok else 'BLOCKED',
            'scope':'DISCOVERY_MATCHED_HEADER_SCOPED_NOT_ECHOED' if report and report.get('discovery_identity_matched') is True else 'UNVERIFIED',
            'balances':'AVAILABLE_SERVER_SIDE_NOT_EQUITY' if ok else 'UNAVAILABLE',
            'orders':'BOUNDED_HISTORY_CROSSCHECKED_NOT_ATOMIC' if venue else 'READ_VERIFIED_COVERAGE_UNVERIFIED' if ok else 'UNAVAILABLE',
            'fees':'RATES_VERIFIED_ASSET_POLICY_PENDING' if venue else 'UNVERIFIED',
            'market_rules':'MINIMUMS_VERIFIED_MAXIMUM_PENDING' if venue else 'FULL_LIMITS_UNVERIFIED','risk':'LIVE_ADMISSION_CONTEXT_REQUIRED',
            'execution':'HTTPS_IMPLEMENTED_LIVE_LOCKED','observed_at':int(time.time()),
            'blockers':[] if report is None else list(dict.fromkeys([v for v in report['blockers'] if not venue or v not in ('ACTUAL_FEES_UNVERIFIED','ORDER_COVERAGE_UNVERIFIED')]+([] if not venue else venue['blockers']))),'action':'NO_TRADE','executable_ready':False}
        Ledger(state/'audit.sqlite').record_event('demo_readonly_status',account_id=PARTITION,
            run_id='local-demo',trace_id='sanitized-status',at=datetime.now(timezone.utc),payload=payload)
    try:
        publish()
        if stop.is_set():return
        account_report=check_configured_account(settings)
        publish(account_report)
        from wallex.execution.preflight import configured_exchange,collect_venue,readiness_report
        try:
            exchange=configured_exchange(settings,clock=lambda:datetime.now(timezone.utc))
            capture=collect_venue(exchange,symbol=settings.wallex_symbol,base_asset=settings.assets[0],quote_asset=settings.assets[1])
            readiness=readiness_report(capture)
            publish(account_report,readiness)
        except Exception as error:
            code=str(error)
            readiness={'status':'BLOCKED','blockers':[code if re.fullmatch(r'[A-Z0-9_]{1,100}',code) else 'VENUE_READINESS_CAPTURE_FAILED'],
                'action':'NO_TRADE','executable_ready':False}
        Ledger(state/'audit.sqlite').record_event('execution_readiness_checked',account_id=PARTITION,
            run_id='local-demo',trace_id='sanitized-status',at=datetime.now(timezone.utc),payload=readiness)
        if stop.is_set():return
        from wallex.execution.simulation import run_simulation
        directory=state.parent/'execution-simulations'/uuid.uuid4().hex;directory.mkdir(parents=True)
        simulated=run_simulation(directory/'simulation.sqlite',partial_exit=True)
        Ledger(state/'audit.sqlite').record_event('execution_simulation_completed',account_id=PARTITION,
            run_id='local-demo',trace_id='synthetic-execution',at=datetime.now(timezone.utc),payload=simulated)
        until=time.monotonic()+90
        while not stop.is_set():
            data=snapshot(state)
            if data['last_observation'] is not None:break
            if stop.wait(.5) or time.monotonic()>until:return
        if stop.is_set() or not settings.boxapi_api_key:return
        from wallex.ai_provider.tp1_review import main as review
        sha=os.environ.get('QF_SOURCE_COMMIT','')
        with redirect_stdout(StringIO()):
            review(['--state-dir',str(state),'--source-commit',sha,'--assess-observation'])
    except Exception:
        print('{"diagnostics":"FAILED_CLOSED","action":"NO_TRADE"}',flush=True)


def serve(settings):
    from wallex.audit.writer import account_writer
    from .observer_api import main as api_main
    directory,state=_paths(settings)
    directory.mkdir(parents=True,exist_ok=True)
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',settings.backend_port))
        except OSError:raise ConfigurationError('PORT_IN_USE_NO_OTHER_PROCESS_STOPPED') from None
    with account_writer(directory/'demo.lock','local-demo'):
        path=directory/'control.json'
        if path.exists():_control(path) # Never overwrite unrelated control data.
        control={'version':VERSION,'instance':uuid.uuid4().hex,'token':uuid.uuid4().hex,
                 'port':settings.backend_port,'pid':os.getpid()}
        path.write_text(canonical_json(control))
        os.environ['QF_DEMO_INSTANCE']=control['instance']
        os.environ['QF_SOURCE_COMMIT']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        stop=threading.Event()
        request=directory/('stop-'+control['instance']+'.json')
        def monitor():
            while not stop.wait(.25):
                if request.exists():
                    try:
                        if json.loads(request.read_text())=={'instance':control['instance'],'token':control['token']}:
                            stop.set();return
                    except Exception:pass
        threading.Thread(target=monitor,daemon=True).start()
        threading.Thread(target=_diagnostics,args=(settings,state,stop),daemon=True).start()
        base,quote=settings.assets
        try:
            return api_main(['--host','127.0.0.1','--port',str(settings.backend_port),'--state-dir',str(state),
                '--frontend-dir',str(ROOT/'frontend/dist'),'--fetch-public','--symbol',settings.wallex_symbol,
                '--base',base,'--quote',quote,'--volume-unit','base'],shutdown_event=stop)
        finally:
            stop.set()
            if path.exists() and _control(path)['instance']==control['instance']:path.unlink()
            if request.exists():request.unlink()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    for name in ('describe','serve','stop'):action.add_argument('--'+name,action='store_true')
    parser.add_argument('--port',type=int)
    args=parser.parse_args(argv)
    try:
        settings=load_settings()
        if args.port is not None:
            if not 1024<=args.port<=65535:raise ConfigurationError('BACKEND_PORT_INVALID')
            from dataclasses import replace
            settings=replace(settings,backend_port=args.port)
        if args.describe:print(canonical_json(describe(settings)));return 0
        if args.stop:print(canonical_json(stop_demo(settings)));return 0
        return serve(settings)
    except ConfigurationError as error:
        print(canonical_json({'status':'CONFIGURATION_BLOCKED','reason':str(error),'action':'NO_TRADE'}));return 1
    except Exception:
        print('{"status":"DEMO_FAILED_CLOSED","action":"NO_TRADE"}');return 1


if __name__=='__main__':raise SystemExit(main())
