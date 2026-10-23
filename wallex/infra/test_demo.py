"""Local-only control and public projection regression; no provider calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from .config import load_settings
from .demo import VERSION, stop_demo
from .observer_api import _demo_projection


class DemoTests(unittest.TestCase):
    def test_cooperative_stop_preserves_evidence_and_public_summary_has_no_private_values(self):
        with TemporaryDirectory() as directory:
            env=Path(directory)/'.env'
            env.write_text('DATA_DIR='+directory+'\n')
            settings=load_settings(env,environ={})
            control_dir=settings.data_dir/'demo-control';control_dir.mkdir()
            control={'version':VERSION,'instance':'a'*32,'token':'b'*32,'port':18091}
            path=control_dir/'control.json';path.write_text(json.dumps(control))
            evidence=settings.data_dir/'saved-evidence.json';evidence.write_text('preserve')
            stopped=threading.Event()
            def worker():
                request=control_dir/('stop-'+control['instance']+'.json')
                while not stopped.wait(.01):
                    if request.exists():
                        if json.loads(request.read_text())=={'instance':control['instance'],'token':control['token']}:
                            path.unlink();return
            thread=threading.Thread(target=worker);thread.start()
            try:
                with patch('wallex.infra.demo._running',return_value=True):
                    self.assertEqual(stop_demo(settings)['status'],'STOPPED_DATA_PRESERVED')
            finally:
                stopped.set();thread.join(timeout=2)
            self.assertEqual(evidence.read_text(),'preserve')
            projected=_demo_projection({'mode':'SHADOW','account':'AUTHENTICATED_READS_VERIFIED',
                'account_id':'synthetic-private-identity','api_key':'synthetic-key','balances':{'amount':'123'},
                'observed_at':1800000,'action':'BUY','executable_ready':True})
            self.assertNotIn('synthetic',json.dumps(projected))
            self.assertEqual(projected['action'],'NO_TRADE')
            self.assertFalse(projected['executable_ready'])
