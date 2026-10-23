"""Real local HTTP, synthetic public observer source; no Wallex network/key."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import observer_api as api
from .agent_runtime import run_observer, PARTITION
from .test_agent_runtime import config, sources, AS_OF
from wallex.audit.ledger import Ledger


@contextmanager
def source():
    with TemporaryDirectory() as directory:
        cfg=config(directory)
        history,book,_=sources()
        run_observer(cfg,fetch_public=True,once=True,clock=lambda:AS_OF,
                     history_transport=history,book_transport=book,emit=lambda _:None)
        yield cfg,Path(directory)


class ObserverApiTests(unittest.TestCase):
    def test_projection_reuses_real_ledger_excludes_private_events_and_never_writes(self):
        with source() as (cfg,_):
            ledger=Ledger(cfg.state_dir/'audit.sqlite')
            raw=next(event.payload for event in reversed(ledger.events()) if event.kind=='observer_cycle_completed')
            raw=dict(raw)
            raw.update(headers={'x-api-key':'SYNTHETIC_SECRET'},account_id='PRIVATE_ID')
            raw['observation_failures']=['m1:PUBLIC_NETWORK_TIMEOUT']
            ledger.record_event('observer_cycle_completed',account_id=PARTITION,run_id='synthetic-public',trace_id='synthetic-public',
                at=datetime.fromtimestamp(AS_OF,timezone.utc),payload=raw)
            for account,kind in ((PARTITION,'preflight_summary'),('synthetic-private-account','observer_cycle_completed')):
                ledger.record_event(kind,account_id=account,run_id='synthetic-private',trace_id='synthetic-private',
                    at=datetime.fromtimestamp(AS_OF,timezone.utc),payload={'api_key':'SYNTHETIC_SECRET','balance':'PRIVATE_VALUE','account_id':'PRIVATE_ID'})
            path=cfg.state_dir/'audit.sqlite'
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            response=api.snapshot(cfg.state_dir,now=AS_OF)
            self.assertEqual(response['data_status'],'RECENT_CAPTURED')
            self.assertEqual(response['runtime']['state'],'STOPPED')
            self.assertEqual(response['runtime']['process_liveness'],'UNVERIFIED')
            observed=response['last_observation']
            self.assertEqual(observed['pipeline']['validation']['h1_candles'],264)
            self.assertGreater(observed['pipeline']['summary']['evaluated_steps'],0)
            self.assertEqual(set(observed['source_hashes']),{'m1','h1','depth'})
            self.assertEqual(observed['observation_failures'],['m1:PUBLIC_NETWORK_TIMEOUT'])
            self.assertTrue(observed['pipeline']['evaluated_steps'][-1]['technical_evidence']['trend']['reasons'])
            self.assertEqual(response['action'],'NO_TRADE')
            self.assertFalse(response['executable_ready'])
            for value in ('SYNTHETIC_SECRET','PRIVATE_VALUE','PRIVATE_ID','synthetic-private-account',str(cfg.state_dir)):
                self.assertNotIn(value,json.dumps(response))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_missing_stale_future_and_tampered_state_fail_closed(self):
        with source() as (cfg,directory):
            for age,status in ((1200,'RECENT_CAPTURED'),(1201,'STALE'),(-1,'UNAVAILABLE')):
                with self.subTest(age=age):
                    response=api.snapshot(cfg.state_dir,now=AS_OF+age)
                    self.assertEqual(response['data_status'],status)
                    self.assertFalse(response['executable_ready'])
            marker=cfg.state_dir/'observer.json'
            marker.write_text('{"private":"SYNTHETIC_SECRET"}')
            self.assertNotIn('SYNTHETIC_SECRET',json.dumps(api.snapshot(cfg.state_dir,now=AS_OF)))
            missing=directory/'must-not-create'
            self.assertEqual(api.snapshot(missing,now=AS_OF)['data_status'],'UNAVAILABLE')
            self.assertFalse(missing.exists())

    def test_http_only_exposes_public_projection_and_safe_static_files(self):
        with source() as (cfg,directory):
            frontend=directory/'frontend'
            frontend.mkdir()
            (frontend/'index.html').write_text('<h1>Synthetic frontend</h1>')
            (frontend/'.env').write_text('SYNTHETIC_SECRET')
            (frontend/'private.json').write_text('{"private":"SYNTHETIC_SECRET"}')
            server=api.Server(('127.0.0.1',0),cfg.state_dir,frontend)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            url='http://127.0.0.1:'+str(server.server_port)
            try:
                with urlopen(url+'/api/observer',timeout=3) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
                    self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
                    self.assertFalse(json.load(response)['executable_ready'])
                with urlopen(url+'/?view=demo',timeout=3) as response:
                    self.assertIn(b'Synthetic frontend',response.read())
                for path in ('/api/account','/api/observer?account=anything','/.env','/private.json','/../observer.json','/%2e%2e/observer.json'):
                    with self.subTest(path=path),self.assertRaises(HTTPError) as error:
                        urlopen(url+path,timeout=3)
                    self.assertEqual(error.exception.code,404)
                    error.exception.close()
                for method in ('POST','PUT','PATCH','DELETE','OPTIONS'):
                    with self.subTest(method=method),self.assertRaises(HTTPError) as error:
                        urlopen(Request(url+'/api/observer',method=method),timeout=3)
                    self.assertEqual(error.exception.code,405)
                    error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
