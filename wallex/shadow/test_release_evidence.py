"""Offline deterministic release packaging; injected synthetic data, no HTTP."""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wallex.backtest.engine import SimulationConfig
from wallex.infra.agent_runtime import run_observer
from wallex.infra.test_agent_runtime import config,sources,AS_OF
from wallex.market.evaluation import EvidenceKind
from .release_evidence import build_evidence


class ReleaseEvidenceTests(unittest.TestCase):
    def fixture(self,directory):
        cfg=config(directory)
        history,book,_=sources()
        run_observer(cfg,fetch_public=True,once=True,clock=lambda:AS_OF,evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE,
                     history_transport=history,book_transport=book,emit=lambda _:None)
        return next((cfg.state_dir/'observations').iterdir())

    def settings(self):
        return dict(symbol='BTCTMN',base='BTC',quote='TMN',as_of=AS_OF,source_commit='a'*40,
                    simulation=SimulationConfig(Decimal('1000000'),Decimal('0.001'),Decimal('0.002'),0))

    def test_real_engines_export_hash_pinned_deterministic_no_trade_evidence(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            source=self.fixture(root)
            a=build_evidence(source,root/'a',**self.settings())
            b=build_evidence(source,root/'b',**self.settings())
            self.assertEqual(a,b)
            c=build_evidence(root/'a'/'public-source',root/'c',**self.settings())
            self.assertEqual(a,c)  # Package alone reproduces every hash, no original state needed.
            self.assertEqual(a['evidence_kind'],'synthetic_fixture')
            self.assertGreater(a['technical_summary']['evaluated_steps'],0)
            self.assertEqual(len(a['runs']),1)
            self.assertEqual(a['runs'][0]['backtest'].fill_count,0)
            self.assertTrue(a['runs'][0]['comparison'].matches)
            self.assertEqual(a['ai']['provider_calls'],0)
            self.assertFalse(a['executable_ready'])
            for name,digest in a['artifacts'].items():
                self.assertEqual(sha256((root/'a'/name).read_bytes()).hexdigest(),digest)
            rows=(root/'a'/'shadow-0'/'events.jsonl').read_text().splitlines()
            self.assertTrue(all('as_of' in json.loads(row) for row in rows))
            with self.assertRaises(ValueError):
                build_evidence(source,root/'a',**self.settings())

    def test_tampered_hash_missing_volume_cutoff_and_commit_fail_closed(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            source=self.fixture(root)
            original=(source/'report.json').read_text()
            for case in ('hash','volume','cutoff','commit'):
                with self.subTest(case=case):
                    raw=json.loads(original)
                    settings=self.settings()
                    if case=='hash':raw['requests']['m1']['raw_sha256']='0'*64
                    if case=='volume':raw['volume_unit']=None
                    if case=='cutoff':settings['as_of']+=900
                    if case=='commit':settings['source_commit']='not-a-commit'
                    (source/'report.json').write_text(json.dumps(raw))
                    out=root/case
                    with self.assertRaises(ValueError):build_evidence(source,out,**settings)
                    self.assertFalse(out.exists())
