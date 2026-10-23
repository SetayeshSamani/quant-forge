"""Explicit synthetic Risk fixture shared by regression tests and offline demo.
No account reads, inferred equity, production defaults or network.
"""
from decimal import Decimal
from types import SimpleNamespace
from wallex.strategy.indicators import Timeframe
from . import loss_guards as loss, pipeline as pipe, portfolio_guards as portfolio, sizing
D=Decimal
AS_OF=1800000


def synthetic_pipeline_inputs(profile=sizing.RiskProfile.L0):
    """Explicit synthetic evidence, never defaults for a production account.

    H1 closes 100..349 give positive trend. Twenty M15 closes of 100 then
    101 give a crossover; all ranges 98..102 give ATR=4. Pe=110 gives
    stop=min(98-.1*4, 110-1.5*4)=97.6. Extra cost=.1 gives unit loss=12.5.
    Thus equity=10000 gives L0 risk cap 10/12.5=.8, R0 cap 25/12.5=2.
    These histories do not claim verified cross-timeframe aggregation.
    """
    def bar(timeframe, index, count):
        hourly = timeframe is Timeframe.H1
        close = D(100 + index) if hourly else D(101 if index == count - 1 else 100)
        return SimpleNamespace(
            symbol="SYNTHETIC-BASE-QUOTE", timeframe=timeframe,
            timestamp=AS_OF - (count - index) * timeframe.value,
            open=close if hourly else D(100),
            high=close + 1 if hourly else D(102),
            low=close - 1 if hourly else D(98), close=close,
            volume=D(4 if hourly else 1), completed=True,
        )
    frame = SimpleNamespace(
        schema_version="market-frame-proposal-v0", symbol="SYNTHETIC-BASE-QUOTE",
        base_asset="BASE", quote_asset="QUOTE", as_of=AS_OF,
        candles_15m=tuple(bar(Timeframe.M15, i, 21) for i in range(21)),
        candles_1h=tuple(bar(Timeframe.H1, i, 250) for i in range(250)),
        dataset_sha256_15m="a" * 64, dataset_sha256_1h="b" * 64,
        data_quality="INCOMPLETE",
        quality_reasons=("QUOTE_UNAVAILABLE", "DEPTH_UNAVAILABLE",
                         "FRAME_TIME_SEMANTICS_PENDING", "SNAPSHOT_HASH_POLICY_PENDING",
                         "SHARED_SCHEMA_PENDING"),
        event_time=None, received_time=None, best_bid=None, best_ask=None,
        spread_pct=None, depth_valid=False, snapshot_hash=None,
    )
    return pipe.PipelineInputs(
        frame=frame, pe=D(110), worst_case_extra_loss_per_unit=D(".1"),
        equity=D(10000), free_quote=D(10000), quote_asset="QUOTE",
        account_id="synthetic-account", profile=profile,
        loss_state=loss.LossGuardState(
            current_equity=D(10000), day_start_equity=D(10000),
            high_water_equity=D(10000), quote_asset="QUOTE", as_of=AS_OF,
            day_start_at=AS_OF // 86400 * 86400, equity_observed_at=AS_OF,
            policy=loss.loss_policy_for(profile),
            prior_daily_breach=False, prior_drawdown_breach=False,
        ),
        portfolio_state=portfolio.PortfolioGuardState(
            account_id="synthetic-account", quote_asset="QUOTE", as_of=AS_OF,
            snapshot_observed_at=AS_OF, max_snapshot_age_seconds=30,
            open_position_count=0, outstanding_order_count=0, uncertain_order_count=0,
            held_reservation_count=0, unsettled_fill_count=0, positions_complete=True,
            orders_complete=True, reservations_complete=True,
            settlement_complete=True, account_scope_complete=True,
        ),
    )
