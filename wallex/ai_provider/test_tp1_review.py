"""Real offline TP-1 pipeline with injected synthetic AI; no provider calls."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from wallex.market.evaluation import EvidenceKind
from wallex.shadow.offline_tp1 import run_offline
from wallex.shadow.test_offline_tp1 import synthetic_files
from wallex.shadow.tp1 import tp1_decision
from wallex.strategy.test_frame_adapter import frame_fixture
from .client import BoxAPIClient, HttpResponse
from .test_client import KEY, reply, envelope, advice
from .tp1_review import CandidateAdviser, main


class TP1ReviewTests(unittest.TestCase):
    def adviser(self, transport):
        return CandidateAdviser(BoxAPIClient(api_key=KEY, transport=transport), source_commit='a'*40,
            evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE, source_hashes={'m1':'b'*64,'h1':'c'*64}, clock=lambda:1800001)

    def test_actual_offline_pipeline_hash_bound_veto_and_replay_cache(self):
        for decision,codes in (('REVIEW',['REVIEW_REQUIRED']),('NO_TRADE',['INSUFFICIENT_EVIDENCE'])):
            with self.subTest(decision=decision), TemporaryDirectory() as directory:
                m1,h1,options=synthetic_files(directory)
                transport=Mock();transport.post.return_value=reply(envelope(content=json.dumps(advice(decision=decision,reason_codes=codes))))
                adviser=self.adviser(transport)
                result=run_offline(m1,h1,**options,adviser=adviser)
                again=run_offline(m1,h1,**options,adviser=adviser)
                self.assertEqual(result,again)
                self.assertEqual(adviser.calls,1)
                self.assertEqual(result['summary']['ai_reviewed_candidates'],1)
                self.assertEqual(result['summary']['ai_retained_candidates'],int(decision=='REVIEW'))
                self.assertEqual(result['summary']['ai_vetoed_candidates'],int(decision=='NO_TRADE'))
                record=adviser.records[0]
                self.assertEqual(record['retained_for_research'],decision=='REVIEW')
                self.assertEqual(record['inference_source'],'SYNTHETIC_TEST_TRANSPORT')
                self.assertEqual(record['as_of'],options['as_of'])
                self.assertEqual(len(record['evidence_hash']),64)
                self.assertFalse(record['executable_ready'])
                self.assertEqual(record['action'],'NO_TRADE')
                context=json.loads(json.loads(transport.post.call_args.args[0].body)['messages'][1]['content'])
                self.assertIsInstance(context['last_close'],str)
                self.assertEqual(context['risk'], 'UNVERIFIED')
                self.assertNotIn(KEY,json.dumps(result))
                self.assertTrue(all(step['action']=='NO_TRADE' for step in result['evaluated_steps']))
                self.assertEqual(result['evaluated_steps'][0]['ai']['advisory_decision'],decision)

    def test_integrated_failures_invalid_frames_and_call_budget_never_authorize(self):
        for response,status in ((HttpResponse(401,b'{}'),'AUTHENTICATION_FAILED'),
                (HttpResponse(403,b'{}'),'FORBIDDEN'),(HttpResponse(429,b'{}'),'RATE_LIMITED'),
                (HttpResponse(200,b'{}'),'INVALID_RESPONSE'),(TimeoutError(),'TIMEOUT')):
            with self.subTest(status=status):
                transport=Mock()
                if isinstance(response,Exception):transport.post.side_effect=response
                else:transport.post.return_value=response
                adviser=self.adviser(transport)
                frame=frame_fixture()
                result=tp1_decision(frame,adviser=adviser)
                self.assertEqual(json.loads(result.diagnostics_json)['ai']['status'],status)
                self.assertEqual(result.action.value,'NO_TRADE')
                self.assertIsNone(result.quantity_base)
                changed=deepcopy(frame);changed.dataset_sha256_15m='d'*64
                self.assertEqual(json.loads(tp1_decision(changed,adviser=adviser).diagnostics_json)['ai']['status'],'CALL_BUDGET_EXHAUSTED')
                invalid=deepcopy(frame);invalid.as_of=-1
                self.assertEqual(json.loads(tp1_decision(invalid,adviser=adviser).diagnostics_json)['ai']['status'],'NOT_A_TECHNICAL_CANDIDATE')
                self.assertEqual(transport.post.call_count,1)
        with unittest.mock.patch('getpass.getpass') as prompt:
            self.assertEqual(main(['--state-dir','unused','--source-commit','a'*40]),2)
            prompt.assert_not_called()

    def test_zero_candidate_observation_calls_model_without_retaining_a_candidate(self):
        from dataclasses import replace
        for decision,codes in (("REVIEW",["REVIEW_REQUIRED"]),("NO_TRADE",["NO_SIGNAL"])):
            with self.subTest(decision=decision):
                transport=Mock();transport.post.return_value=reply(envelope(content=json.dumps(advice(decision=decision,reason_codes=codes))))
                adviser=CandidateAdviser(BoxAPIClient(api_key=KEY,transport=transport),source_commit='a'*40,
                    evidence_kind=EvidenceKind.HISTORICAL_DATA,source_hashes={'m1':'b'*64,'h1':'c'*64},
                    clock=lambda:1800001,assess_observation=True)
                frame=frame_fixture()
                frame.candles_15m=tuple(replace(bar,close=bar.open) for bar in frame.candles_15m)
                result=tp1_decision(frame,adviser=adviser)
                self.assertFalse(json.loads(result.reason)['technical_eligible'])
                row=json.loads(result.diagnostics_json)['ai']
                self.assertEqual(transport.post.call_count,1)
                self.assertEqual(row['http_status'],200)
                self.assertEqual(len(row['response_sha256']),64)
                self.assertFalse(row['retained_for_research'])
                self.assertEqual(row['advisory_decision'],decision)
                self.assertEqual(row['assessment_mode'],'TP1_OBSERVATION_RESEARCH_ONLY')
                context=json.loads(json.loads(transport.post.call_args.args[0].body)['messages'][1]['content'])
                self.assertEqual(__import__('decimal').Decimal(context['distance_m15']),0)
                self.assertFalse(context['technical_eligible'])
                self.assertEqual(result.action.value,'NO_TRADE')
                self.assertFalse(row['executable_ready'])
                self.assertNotIn(KEY,json.dumps(row))

    def test_actual_persistence_readback_and_explicit_provider_failures(self):
        from .tp1_review import persist_review
        from wallex.audit.ledger import Ledger
        from wallex.infra.agent_runtime import PARTITION
        for response,status in ((HttpResponse(401,b'{}'),'AUTHENTICATION_FAILED'),
            (HttpResponse(403,b'Request limit reached. You have no more API requests left.'),'REQUEST_LIMIT_REACHED'),
            (HttpResponse(429,b'{}'),'RATE_LIMITED'),(HttpResponse(200,b'{}'),'INVALID_RESPONSE')):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                transport=Mock();transport.post.return_value=response
                adviser=self.adviser(transport)
                result=tp1_decision(frame_fixture(),adviser=adviser)
                row=json.loads(result.diagnostics_json)['ai']
                self.assertEqual(row['status'],status)
                self.assertEqual(row['http_status'],response.status)
                self.assertIsNone(row['response_sha256'])
                report={'records':[row],'action':'NO_TRADE','executable_ready':False}
                verified=persist_review(directory,report)
                self.assertTrue(verified['audit_readback_verified'])
                event=next(e for e in Ledger(Path(directory)/'audit.sqlite').events() if e.event_id==verified['audit_event_id'])
                self.assertEqual(event.account_id,PARTITION)
                self.assertEqual(event.payload,report)
                self.assertNotIn(KEY,event.payload_json)
