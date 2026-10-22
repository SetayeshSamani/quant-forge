"""Injected deterministic execution fixture, not a backtester or Shadow Runner.

All order IDs and observations come from supplied synthetic scenarios. No market
matching, price generation, network, credentials or exchange operation is present.
"""

from collections import deque
from dataclasses import dataclass

from .models import ContractError, Mode, OrderObservation, TradeIntent, json_value


class ReplayUncertain(TimeoutError):
    pass


@dataclass(frozen=True)
class ReplayScenario:
    submission: OrderObservation
    lose_response_after_accept: bool = False
    timeout_before_accept: bool = False
    cancellation: OrderObservation | None = None

    def __post_init__(self):
        if not isinstance(self.submission, OrderObservation):
            raise ContractError("Supply an explicit synthetic submission observation.")
        if type(self.lose_response_after_accept) is not bool or type(self.timeout_before_accept) is not bool:
            raise ContractError("Replay fault switches must be booleans.")


class ReplayTransport:
    """Trusted test dependency; mutable venue fixture state is separate from the DB.

    Restart tests inject the same venue fixture or an explicit saved evidence tape.
    This in-memory fixture is not a durable exchange emulator or account ledger.
    """

    def __init__(self, scenarios: dict[tuple[str, str], ReplayScenario], *, lookups=None):
        self._scenarios = dict(scenarios)
        self._orders: dict[tuple[str, str], OrderObservation] = {}
        self._lookups = {key: deque(events) for key, events in (lookups or {}).items()}
        self.submit_calls: list[tuple[str, str]] = []
        self.lookup_calls: list[tuple[str, str]] = []
        self.cancel_calls: list[tuple[str, str]] = []

    @staticmethod
    def _copy(observation):
        if not isinstance(observation, OrderObservation):
            raise ContractError("Expected explicit synthetic source evidence.")
        return OrderObservation.from_dict(json_value(observation))

    def submit(self, intent: TradeIntent) -> OrderObservation:
        if intent.mode != Mode.REPLAY:
            raise ContractError("Replay fixture refuses other execution modes.")
        key = (intent.account_id, intent.client_id)
        self.submit_calls.append(key)
        scenario = self._scenarios.get(key)
        if scenario is None or scenario.timeout_before_accept:
            raise ReplayUncertain("synthetic_submission_response_unavailable")
        if key in self._orders:
            raise ReplayUncertain("synthetic_duplicate_transport_call")
        observation = self._copy(scenario.submission)
        self._orders[key] = observation
        if scenario.lose_response_after_accept:
            raise ReplayUncertain("synthetic_response_lost_after_acceptance")
        return self._copy(observation)

    def lookup(self, account_id: str, client_id: str) -> OrderObservation | None:
        key = (account_id, client_id)
        self.lookup_calls.append(key)
        tape = self._lookups.get(key)
        if tape:
            event = tape.popleft()
            if event == "timeout":
                raise ReplayUncertain("synthetic_lookup_timeout")
            if event is None:
                return None
            self._orders[key] = self._copy(event)
        result = self._orders.get(key)
        return self._copy(result) if result is not None else None

    def cancel(self, account_id: str, client_id: str) -> OrderObservation:
        key = (account_id, client_id)
        self.cancel_calls.append(key)
        scenario = self._scenarios.get(key)
        if scenario is None or scenario.cancellation is None:
            raise ReplayUncertain("synthetic_cancellation_unconfirmed")
        self._orders[key] = self._copy(scenario.cancellation)
        return self._copy(self._orders[key])
