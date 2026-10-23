"""Runnable SYNTHETIC network-tape lifecycle using the actual gateway and ledger.
Never constructs HTTPS transport, loads credentials or treats fills as real.
"""
import argparse
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from wallex.audit.ledger import Ledger
from .models import Mode,Side,Reservation,canonical_json
from .live import WallexGateway,LiveEvidence,REQUIRED_PROOFS
from .wallex_http import WallexExchange
from .synthetic import D,KEY,ACCOUNT,owner,setup_inputs,HttpTape


def run_simulation(path,*,fault='timeout',partial_exit=False):
    path=Path(path)
    if path.exists():raise ValueError('NEW_SEPARATE_SIMULATION_LEDGER_REQUIRED')
    request,checks,policy,now=setup_inputs();tape=HttpTape(now)
    def exchange():return WallexExchange(api_key=KEY,account_id=ACCOUNT,owner_user_id=7,transport=tape,clock=lambda:now)
    wire=exchange();ledger=Ledger(path,mode=Mode.LIVE);gateway=WallexGateway(ledger,wire,clock=lambda:now)
    def evidence(c):return LiveEvidence(c,wire.balances(),tuple((k,'a'*64) for k in sorted(REQUIRED_PROOFS)),'HTTP_MOCK',D('.01'),D(10),'TMN')
    gateway.authorize_entry(request,evidence(checks),policy,owner_confirmation=owner(request))
    tape.fault=fault
    states=[]
    try:states.append(gateway.submit(request).receipt.state.value)
    except KeyboardInterrupt:states.append('SUBMITTING_CRASH')
    wire=exchange();gateway=WallexGateway(Ledger(path,mode=Mode.LIVE),wire,clock=lambda:now)
    states.append(gateway.recover_account(ACCOUNT,run_id='synthetic-restart',trace_id='synthetic-restart')[0].state.value)
    duplicate=gateway.submit(request).disposition
    tape.fill('BUY_1',D('.25'));states.append(gateway.reconcile('live_buy').state.value)
    quantity=ledger.receipt('live_buy').known_net_acquired_base
    sell=replace(request,intent=replace(request.intent,intent_id='live_sell',client_id='SELL_1',side=Side.SELL,
        purpose='risk_reducing_exit',quantity_base=quantity,price_quote_per_base=D(97)),reservation=Reservation('BTC',quantity,quantity))
    exit_checks=replace(checks,bid=D(97),ask=D(98))
    protection=gateway.inspect_protection('live_buy',evidence(exit_checks))
    gateway.authorize_exit('live_buy',sell,evidence(exit_checks),owner_confirmation=owner(sell))
    states.append(gateway.submit(sell).receipt.state.value)
    monitoring=[gateway.inspect_protection('live_buy',evidence(exit_checks))['status']]
    exit_id='live_sell'
    if partial_exit:
        tape.fill('SELL_1',quantity/2);states.append(gateway.reconcile('live_sell').state.value)
        now=now+timedelta(seconds=31);tape.now=now
        exit_checks=replace(exit_checks,observed_at=now)
        monitoring.append(gateway.inspect_protection('live_buy',evidence(exit_checks))['status'])
        states.append(gateway.cancel('live_sell',evidence=evidence(exit_checks),owner_confirmation=owner(sell,'CANCEL')).state.value)
        sell=replace(sell,intent=replace(sell.intent,intent_id='live_sell2',client_id='SELL_2',quantity_base=quantity/2,
            created_at=now,expires_at=now+timedelta(seconds=20)),reservation=Reservation('BTC',quantity/2,quantity/2))
        gateway.authorize_exit('live_buy',sell,evidence(exit_checks),owner_confirmation=owner(sell))
        states.append(gateway.submit(sell).receipt.state.value)
        tape.fill('SELL_2',quantity/2);exit_id='live_sell2'
    else:tape.fill('SELL_1',quantity)
    states.append(gateway.reconcile(exit_id).state.value)
    settlement=gateway.settle('live_buy',exit_id,evidence(exit_checks))
    return {'mode':'SIMULATION','evidence_kind':'synthetic_fixture','transport':'HTTP_MOCK',
        'states':states,'buy_submissions':sum(r.method=='POST' and b'"BUY"' in r.body for r in tape.requests),
        'sell_submissions':sum(r.method=='POST' and b'"SELL"' in r.body for r in tape.requests),
        'duplicate_prevention':duplicate,'exit_monitoring':monitoring,'protection':protection['status'],'settlement':settlement['status'],'audit_events':len(ledger.events()),
        'real_orders_submitted':0,'action':'NO_TRADE','executable_ready':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--simulate',action='store_true',required=True)
    parser.add_argument('--ledger',type=Path);args=parser.parse_args(argv)
    if args.ledger:result=run_simulation(args.ledger)
    else:
        with TemporaryDirectory(prefix='quantforge-synthetic-') as directory:
            result=run_simulation(Path(directory)/'simulation.sqlite')
    print(canonical_json(result));return 0


if __name__=='__main__':raise SystemExit(main())
