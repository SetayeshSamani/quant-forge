"""Runnable simulation never opens a genuine HTTPS connection."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from .simulation import run_simulation


class SimulationTests(unittest.TestCase):
    def test_complete_lifecycle_recovers_timeout_and_crash_without_network_or_duplicate_post(self):
        for fault,partial_exit in (('timeout',False),('crash',False),('timeout',True)):
            with self.subTest(fault=fault,partial_exit=partial_exit),TemporaryDirectory() as directory:
                with patch('wallex.execution.wallex_http.HTTPSExchangeTransport.send',side_effect=AssertionError('REAL_HTTP_FORBIDDEN')):
                    report=run_simulation(Path(directory)/'simulation.sqlite',fault=fault,partial_exit=partial_exit)
                self.assertEqual(report['mode'],'SIMULATION')
                self.assertEqual(report['buy_submissions'],1);self.assertEqual(report['sell_submissions'],2 if partial_exit else 1)
                if partial_exit:self.assertIn('EXIT_TIMEOUT_CANCEL_REPRICE_REQUIRES_EXACT_APPROVAL',report['exit_monitoring'])
                self.assertEqual(report['settlement'],'MOCK_SETTLED')
                self.assertEqual(report['protection'],'PROTECTIVE_SELL_REQUIRES_EXACT_APPROVAL')
                self.assertEqual(report['real_orders_submitted'],0)
                self.assertFalse(report['executable_ready'])
