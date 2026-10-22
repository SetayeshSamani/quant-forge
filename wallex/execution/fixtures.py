"""Explicit synthetic fixtures, never strategy-produced entries or account evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from wallex.risk import EntryRequest, RiskLimits, evaluate_entry

from .models import (
    DecisionReferences, ExecutionRequest, Fill, Mode, OrderObservation, OrderState,
    Reservation, Side, TradeIntent,
)


FIXTURE_TIME = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)


def synthetic_request(*, intent_id="fixture-intent-1", client_id="fixture-client-1",
                      account_id="fixture-account", mode=Mode.REPLAY) -> ExecutionRequest:
    # Every numeric setting is test input, not a recommended market/risk setting.
    quantity, price = Decimal("2"), Decimal("10")
    decision = evaluate_entry(EntryRequest(quantity, price), RiskLimits(Decimal("3"), Decimal("30")))
    intent = TradeIntent(intent_id, client_id, account_id, "FIXTURE-PAIR", "BASE", "QUOTE",
                         Side.BUY, "LIMIT", quantity, price, FIXTURE_TIME,
                         FIXTURE_TIME + timedelta(seconds=20), mode,
                         "fixture-run", "fixture-trace", "entry")
    return ExecutionRequest(intent, decision,
                            DecisionReferences("fixture-risk-decision", "fixture-risk-version",
                                               "fixture-strategy-version", "fixture-account-reference",
                                               None, "agent_not_integrated_in_fixture"),
                            Reservation("QUOTE", Decimal("21"), Decimal("100")),
                            "synthetic_fixture")


def synthetic_observation(request=None, *, state=OrderState.WORKING, cumulative=Decimal(0),
                          fills=(), observed_at=FIXTURE_TIME, source_order_id="fixture-order-1"):
    intent = (request or synthetic_request()).intent
    return OrderObservation(intent.account_id, intent.client_id, intent.symbol, "synthetic-venue",
                            source_order_id, state, cumulative, tuple(fills), observed_at,
                            observed_at, None)


def synthetic_fill(request=None, *, fill_id="fixture-fill-1", quantity=Decimal("1"),
                   fee=Decimal("0.01"), fee_asset="BASE", source_time=FIXTURE_TIME,
                   received_at=FIXTURE_TIME, source_order_id="fixture-order-1"):
    intent = (request or synthetic_request()).intent
    return Fill("synthetic-venue", fill_id, source_order_id, intent.account_id, intent.client_id,
                intent.symbol, intent.base_asset, intent.quote_asset, quantity,
                intent.price_quote_per_base, fee, fee_asset,
                "fee_not_reported_by_fixture" if fee is None else None,
                source_time, "source_time_not_reported_by_fixture" if source_time is None else None,
                received_at)
