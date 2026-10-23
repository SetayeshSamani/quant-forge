"""Synthetic settings/credentials only; no real .env values or network."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

from .config import load_settings,read_dotenv,ConfigurationError
from .spot_preflight import check_configured_account
from .test_spot_preflight import fixture,FAKE
from .credit_readonly import HttpReply


class ConfigurationTests(unittest.TestCase):
    def test_precedence_quotes_restart_validation_and_safe_repr(self):
        with TemporaryDirectory() as directory:
            env=Path(directory)/'.env'
            content='\ufeffBOXAPI_API_KEY="synthetic_only_key_123456"\nWALLEX_API_KEY=synthetic_wallex\nBOXAPI_MODEL=gpt-5.4-mini # comment\nBACKEND_PORT=18091\nTRADING_MODE=SHADOW\n'
            env.write_text(content,encoding='utf-8')
            settings=load_settings(env,environ={'BACKEND_PORT':'18092'})
            self.assertEqual(settings.backend_port,18092)
            self.assertEqual(settings.boxapi_api_key,'synthetic_only_key_123456')
            self.assertNotIn('synthetic_only_key',repr(settings))
            self.assertNotIn('synthetic_wallex',repr(settings))
            self.assertEqual(env.read_text(encoding='utf-8'),content)
            env.write_text(content.replace('18091','18093'),encoding='utf-8')
            self.assertEqual(load_settings(env,environ={}).backend_port,18093)
            for invalid in ('BOXAPI_BASE_URL=https://evil.invalid','LIVE_TRADING_ENABLED=maybe','BACKEND_PORT=invalid','WALLEX_ACCOUNT_TYPE=main','BOXAPI_API_KEY=x\nBOXAPI_API_KEY=y'):
                with self.subTest(case=invalid.split('=')[0]):
                    env.write_text(invalid)
                    with self.assertRaises(ConfigurationError) as error:load_settings(env,environ={})
                    self.assertNotIn('synthetic',str(error.exception))

    def test_unique_configured_prop_binding_reuses_scoped_reads_and_never_activates(self):
        from .test_spot_preflight import fixture
        _,accounts,balances,orders,market=fixture()
        with TemporaryDirectory() as directory:
            env=Path(directory)/'.env'
            env.write_text('WALLEX_API_KEY='+FAKE+'\nWALLEX_SUBACCOUNT_ID=\n')
            original=env.read_text()
            settings=load_settings(env,environ={})
            transport=Mock()
            transport.get.side_effect=[HttpReply(200,json.dumps(v).encode()) for v in (accounts,accounts,balances,{'success':True,'result':{'orders':[]}})]
            report=check_configured_account(settings,transport=transport,market_get=lambda:market,clock=lambda:1800000,
                discover_account=True,env_file=env)
            self.assertTrue(report['authenticated_reads_succeeded'])
            updated=load_settings(env,environ={})
            self.assertEqual(updated.wallex_subaccount_id,accounts['result'][0]['clientId'])
            self.assertEqual(env.read_text().replace(updated.wallex_subaccount_id,''),original)
            self.assertFalse(report['executable_ready'])
            self.assertNotIn(FAKE,json.dumps(report))
            self.assertTrue(all(req.args[0].path.startswith(('/sub-accounts','/v1/account/')) for req in transport.get.call_args_list))
