from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import unittest

from wallex.execution.fixtures import synthetic_request
from wallex.execution.models import (
    AccountSnapshot, ContractError, ExecutionRequest, Mode, Reservation, exact_product,
)


class ConsumerContractTests(unittest.TestCase):
    def test_stored_request_roundtrip_and_numeric_idempotency_are_exact(self):
        request = synthetic_request()
        self.assertEqual(ExecutionRequest.from_dict(request.to_dict()), request)
        self.assertEqual(request.to_dict()["intent"]["quantity_base"], "2")
        equivalent = replace(request, intent=replace(request.intent, quantity_base=Decimal("2.000")))
        self.assertEqual(request.fingerprint, equivalent.fingerprint)
        changed = replace(request, intent=replace(request.intent, client_id="different"))
        self.assertNotEqual(request.fingerprint, changed.fingerprint)
        with self.assertRaises(FrozenInstanceError):
            request.intent.client_id = "retry-generated"

    def test_decimal_and_explicit_unit_validation(self):
        request = synthetic_request()
        for value in (True, 2, 2.0, "2", Decimal("NaN"), Decimal("Infinity"), Decimal(0)):
            with self.subTest(value=value), self.assertRaises(ContractError):
                replace(request.intent, quantity_base=value)
        for reservation in (Reservation("BASE", Decimal("21"), Decimal("100")),
                            Reservation("QUOTE", Decimal("19"), Decimal("100"))):
            with self.assertRaises(ContractError):
                replace(request, reservation=reservation)

    def test_naive_times_unknown_versions_modes_and_missing_decision_references_fail(self):
        request = synthetic_request()
        for fields in ({"created_at": datetime(2026, 1, 1)}, {"schema_version": "future"},
                       {"mode": "paper"}, {"expires_at": request.intent.created_at}):
            with self.assertRaises(ContractError):
                replace(request.intent, **fields)
        with self.assertRaises(ContractError):
            replace(request.references, risk_policy_version="")
        offset = request.intent.created_at.astimezone(timezone(timedelta(hours=3, minutes=30)))
        self.assertEqual(replace(request.intent, created_at=offset).created_at.utcoffset(), timedelta(0))
        self.assertEqual(Mode.REPLAY.value, "replay")

    def test_arithmetic_does_not_depend_on_decimal_default_precision(self):
        with localcontext() as context:
            context.prec = 4
            result = exact_product(Decimal("0.123456789123456789"), Decimal("100.123456789"))
        self.assertEqual(result, Decimal("12.360920491111110999750190521"))

    def test_order_receipts_cannot_establish_account_balances(self):
        snapshot = AccountSnapshot("fixture-snapshot", "fixture-account",
                                   synthetic_request().intent.created_at, None,
                                   "replay_order_receipts_are_not_reconciled_account_evidence")
        self.assertIsNone(snapshot.balances)
        with self.assertRaises(ContractError):
            replace(snapshot, balances={"BASE": "2"}, unknown_reason=None)


if __name__ == "__main__":
    unittest.main()
