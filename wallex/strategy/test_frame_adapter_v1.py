"""Focused real-model v1 consumer regressions with synthetic evidence only."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import unittest
from unittest.mock import patch

from wallex.market.frame_example import build_synthetic_frame
from wallex.market.frame_v1 import (
    QualityV1, build_market_frame_v1, compute_snapshot_hash,
)
from wallex.market.frame_v1_example import synthetic_inputs_v1
from wallex.risk import gate, loss_guards, pipeline, portfolio_guards, sizing
from wallex.strategy import frame_adapter as adapter
from wallex.strategy.engine import Signal

D = Decimal


def frame_fixture(*, trending=False, **changes):
    inputs = synthetic_inputs_v1()
    if trending:
        # Independent reference: H1 closes 100..349, M15 closes 100 x20 then
        # 101 with range 98..102. ATR=4, Pe=110 -> stop=97.6, unit loss=12.5
        # including explicit extra cost .1; L0 risk budget=10 -> base cap=.8.
        inputs['candles_15m'] = tuple(replace(item, candle=replace(
            item.candle, open=D(100), high=D(102), low=D(98),
            close=D(101 if i == 20 else 100),
        )) for i, item in enumerate(inputs['candles_15m']))
        inputs['candles_1h'] = tuple(replace(item, candle=replace(
            item.candle, open=D(100+i), high=D(101+i), low=D(99+i), close=D(100+i),
        )) for i, item in enumerate(inputs['candles_1h']))
    return build_market_frame_v1(**(inputs | changes))


def rehash(frame):
    """Attacker-controlled checksums alone must not pass semantic validation."""
    return replace(frame, snapshot_hash=compute_snapshot_hash(frame))


def pipeline_inputs(frame):
    cutoff = frame.as_of
    profile = sizing.RiskProfile.L0
    return pipeline.PipelineInputs(
        frame=frame, pe=D(110), worst_case_extra_loss_per_unit=D('.1'),
        equity=D(10000), free_quote=D(10000), quote_asset=frame.quote_asset,
        account_id='synthetic-account', profile=profile,
        loss_state=loss_guards.LossGuardState(
            current_equity=D(10000), day_start_equity=D(10000), high_water_equity=D(10000),
            quote_asset=frame.quote_asset, as_of=cutoff, day_start_at=cutoff//86400*86400,
            equity_observed_at=cutoff, policy=loss_guards.loss_policy_for(profile),
            prior_daily_breach=False, prior_drawdown_breach=False,
        ),
        portfolio_state=portfolio_guards.PortfolioGuardState(
            account_id='synthetic-account', quote_asset=frame.quote_asset,
            as_of=cutoff, snapshot_observed_at=cutoff, max_snapshot_age_seconds=0,
            open_position_count=0, outstanding_order_count=0, uncertain_order_count=0,
            held_reservation_count=0, unsettled_fill_count=0, positions_complete=True,
            orders_complete=True, reservations_complete=True,
            settlement_complete=True, account_scope_complete=True,
        ),
    )


class FrameV1AdapterTests(unittest.TestCase):
    def assert_rejected(self, frame):
        with patch.object(adapter, 'assess_hourly_trend') as trend:
            result = adapter.assess_market_frame(frame, pe=D(110))
        trend.assert_not_called()
        self.assertFalse(result.frame_valid or result.executable_ready)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertIsNone(result.metadata)
        self.assertIsNone(result.stop)
        return result

    def test_valid_v1_preserves_receipts_sources_quote_depth_and_immutable_evidence(self):
        frame = frame_fixture()
        result = adapter.assess_market_frame(frame)
        self.assertTrue(result.frame_valid, result.reasons)
        meta = result.metadata
        self.assertEqual(meta.v1_evidence, frame)
        self.assertIsNot(meta.v1_evidence, frame)
        self.assertEqual(meta.snapshot_hash, frame.snapshot_hash)
        self.assertEqual(meta.v1_evidence.contract_status, 'DRAFT')
        self.assertEqual(meta.data_quality, 'COMPLETE')
        self.assertEqual((meta.event_time, meta.received_time),
                         (frame.event_time, frame.received_time))
        self.assertEqual(meta.v1_evidence.source_ids, frame.source_ids)
        self.assertEqual(meta.best_ask, frame.quote.best_ask)
        self.assertEqual(meta.v1_evidence.depth.asks, frame.depth.asks)
        for history, source in ((meta.m15, frame.candles_15m), (meta.h1, frame.candles_1h)):
            self.assertIsNone(history.dataset_sha256)
            self.assertEqual(tuple(b.close for b in history.candles),
                             tuple(item.candle.close for item in source))
            self.assertEqual(tuple(b.timestamp for b in history.candles),
                             tuple(item.candle.timestamp for item in source))
            self.assertEqual(history.volumes_base, tuple(item.candle.volume for item in source))
        self.assertFalse(result.executable_ready)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertIn(adapter.DiagnosticReason.SHARED_SCHEMA_UNAPPROVED, result.reasons)
        self.assertNotIn(adapter.DiagnosticReason.QUOTE_UNAVAILABLE, result.reasons)
        with self.assertRaises(FrozenInstanceError):
            meta.v1_evidence.quote.best_ask = D(1)
        self.assertEqual(result, adapter.assess_market_frame(frame))

    def test_actual_v0_interface_and_missing_evidence_remain_unchanged(self):
        frame = build_synthetic_frame()
        result = adapter.assess_market_frame(frame)
        self.assertTrue(result.frame_valid)
        self.assertIsNone(result.metadata.v1_evidence)
        self.assertIsNone(result.metadata.snapshot_hash)
        self.assertEqual(result.metadata.m15.dataset_sha256, frame.dataset_sha256_15m)
        self.assertEqual(result.metadata.data_quality, 'INCOMPLETE')
        self.assertIn(adapter.DiagnosticReason.QUOTE_UNAVAILABLE, result.reasons)
        self.assertIn(adapter.DiagnosticReason.SNAPSHOT_HASH_UNAVAILABLE, result.reasons)
        self.assertFalse(result.executable_ready)

    def test_invalid_hash_and_rehashed_derived_field_tampering_rejected(self):
        frame = frame_fixture()
        for digest in (None, '0'*64, 'invalid'):
            with self.subTest(digest=digest):
                result = self.assert_rejected(replace(frame, snapshot_hash=digest))
                self.assertIn(adapter.DiagnosticReason.INVALID_PROVENANCE, result.reasons)
        for changes in ({'received_time': frame.received_time+1}, {'source_ids': ('forged',)},
                        {'spread_pct': D(999)}, {'depth_valid': False},
                        {'quality': QualityV1.INCOMPLETE}):
            with self.subTest(changes=changes):
                self.assert_rejected(rehash(replace(frame, **changes)))

    def test_missing_future_and_stale_candle_availability_fail_closed(self):
        frame = frame_fixture()
        last = frame.candles_15m[-1]
        for receipt in (None, frame.as_of+1, last.candle.timestamp):
            with self.subTest(receipt=receipt):
                bars = (*frame.candles_15m[:-1], replace(last, received_time=receipt))
                self.assert_rejected(rehash(replace(frame, candles_15m=bars)))
        # Histories are contiguous but no longer latest at this explicit cutoff.
        self.assert_rejected(rehash(replace(frame, as_of=frame.as_of+900)))

    def test_incomplete_missing_or_stale_quote_depth_cannot_enter_v1_diagnostics(self):
        inputs = synthetic_inputs_v1()
        quote, depth = inputs['quote'], inputs['depth']
        for changes in (
            {'quote': None}, {'depth': None},
            {'quote': replace(quote, received_time=None)},
            {'depth': replace(depth, received_time=None)},
            {'quote': replace(quote, event_time=None)},
            {'quote': replace(quote, event_time=903590, received_time=903591)},
            {'depth': replace(depth, event_time=903590, received_time=903591)},
        ):
            with self.subTest(changes=changes):
                frame = build_market_frame_v1(**(inputs | changes))
                self.assertIs(frame.quality, QualityV1.INCOMPLETE)
                self.assert_rejected(frame)

    def test_rehashed_candle_identity_timeframe_and_cutoff_tampering_rejected(self):
        frame = frame_fixture()
        first = frame.candles_15m[0]
        for changes in ({'symbol': 'OTHER'}, {'timeframe': frame.candles_1h[0].candle.timeframe}):
            with self.subTest(changes=changes):
                bars = (replace(first, candle=replace(first.candle, **changes)), *frame.candles_15m[1:])
                self.assert_rejected(rehash(replace(frame, candles_15m=bars)))
        self.assert_rejected(rehash(replace(frame, symbol='OTHER')))
        self.assert_rejected(rehash(replace(frame, as_of=903600)))

    def test_pe_remains_explicit_and_complete_quality_never_authorizes(self):
        frame = frame_fixture(trending=True)
        for pe in (None, D('NaN')):
            with self.subTest(pe=pe):
                result = adapter.assess_market_frame(frame, pe=pe)
                self.assertTrue(result.frame_valid and result.technical_eligible)
                self.assertIsNone(result.stop)
                self.assertFalse(result.executable_ready)
        result = adapter.assess_market_frame(frame, pe=D(110))
        self.assertTrue(result.technical_eligible and result.stop.valid)
        self.assertEqual(result.stop.entry_price, D(110))
        self.assertNotEqual(result.stop.entry_price, frame.quote.best_ask)
        self.assertNotEqual(result.stop.entry_price, frame.candles_15m[-1].candle.close)
        self.assertEqual(result.stop.initial_stop, D('97.6'))
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertFalse(result.executable_ready or frame.executable_ready)

    def test_existing_risk_pipeline_accepts_v1_but_never_authorizes_execution(self):
        inputs = pipeline_inputs(frame_fixture(trending=True))
        with patch.object(gate, 'evaluate_entry', side_effect=AssertionError('No Gate')) as check:
            result = pipeline.assess_risk_pipeline(inputs)
        check.assert_not_called()
        self.assertTrue(result.frame.frame_valid and result.coherence_valid)
        self.assertTrue(result.local_conditions_satisfied)
        self.assertEqual(result.sizing.theoretical_quantity_upper_bound, D('.8'))
        self.assertEqual(result.frame.metadata.v1_evidence, inputs.frame)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertFalse(result.executable_ready)
        invalid = replace(inputs, frame=replace(inputs.frame, snapshot_hash=None))
        rejected = pipeline.assess_risk_pipeline(invalid)
        self.assertFalse(rejected.local_conditions_satisfied)
        self.assertIsNone(rejected.sizing)
        self.assertIn(pipeline.PipelineReason.FRAME_REJECTED, rejected.reasons)


if __name__ == '__main__':
    unittest.main()
