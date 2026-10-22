"""Focused offline credit evidence tests; every account/value is synthetic."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from wallex.execution.gateway import ReplayGateway
from wallex.risk import gate
from wallex.strategy.engine import Signal

from . import credit_account as credit

D = Decimal
R = credit.CreditReason


def raw_fixture():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'credit_account_synthetic.json').read_text(encoding='utf-8'))


def inputs(raw=None):
    raw = raw_fixture() if raw is None else raw
    binding = dict(raw['binding'])
    binding['kind'] = credit.AccountKind(binding['kind'])
    semantics = dict(raw['semantics'])
    semantics['balance_units'] = tuple(tuple(pair) for pair in semantics['balance_units'])
    return credit.CreditEvidenceInputs(
        binding=credit.CreditAccountBinding(**binding),
        accounts=credit.CreditResponse(**raw['accounts']), plan=credit.CreditResponse(**raw['plan']),
        balances=credit.CreditResponse(**raw['balances']), semantics=credit.CreditSemantics(**semantics),
        as_of=raw['as_of'], max_age_seconds=raw['max_age_seconds'],
    )


class CreditAccountTests(unittest.TestCase):
    def assert_rejected(self, evidence, reason=None):
        result = credit.assess_credit_account(evidence)
        self.assertFalse(result.snapshot_valid)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.blocks_new_entries)
        self.assertFalse(result.credential_scope_verified or result.executable_ready)
        self.assertIs(result.executable_action, Signal.NO_TRADE)
        self.assertIsNotNone(result.rejected_field)
        if reason is not None:
            self.assertIn(reason, result.reasons)
        return result

    def test_complete_synthetic_evidence_preserves_claims_without_inferring_equity(self):
        evidence = inputs()
        result = credit.assess_credit_account(evidence)
        self.assertTrue(result.snapshot_valid, result)
        snapshot = result.snapshot
        self.assertEqual(snapshot.binding, evidence.binding)
        self.assertEqual(snapshot.available_quote, D(2750))
        self.assertEqual(snapshot.collateral, D(1000))
        self.assertEqual(snapshot.loan_principal, D(2000))
        self.assertEqual(snapshot.outstanding_debt_claim, D(2002))
        self.assertEqual((snapshot.daily_fee, snapshot.daily_fee_discount,
                          snapshot.daily_fee_after_discount, snapshot.payable_fee),
                         (D('2.5'), D('.5'), D(2), D(2)))
        self.assertEqual((snapshot.warning_limit, snapshot.liquidation_limit), (D(2100), D(2050)))
        self.assertEqual(snapshot.expires_at, 1886400)
        self.assertEqual(snapshot.observed_times, (('accounts',1799990),('plan',1799995),('balances',1799998)))
        self.assertIsNone(snapshot.risk_equity)
        self.assertTrue(result.blocks_new_entries)
        self.assertFalse(result.credential_scope_verified or result.executable_ready)
        self.assertIn('CREDIT_SEMANTICS_UNAPPROVED', result.limitations)
        self.assertIn('NON_ATOMIC_SNAPSHOT', result.limitations)

    def test_wrong_account_kind_owner_and_package_identity_fail_closed(self):
        evidence = inputs()
        for changes in ({'kind':'MAIN'}, {'sub_account_client_id':'other-account'},
                        {'owner_user_id':43}, {'owner_user_id':True}, {'package_id':13},
                        {'user_package_id':35}, {'plan_type':'margin'}):
            with self.subTest(binding=changes):
                self.assert_rejected(replace(evidence, binding=replace(evidence.binding, **changes)),
                                     R.IDENTITY_MISMATCH)
        raw = raw_fixture()
        raw['accounts']['payload']['result'][0]['type'] = 'trading_bot_trade'
        self.assert_rejected(inputs(raw), R.IDENTITY_MISMATCH)

    def test_inactive_suspended_or_terminal_plan_never_passes_as_active(self):
        for name,value in (('isActive',False), ('isActive',1), ('isSuspend',True), ('isSuspend',0)):
            with self.subTest(field=name):
                raw = raw_fixture()
                raw['accounts']['payload']['result'][0][name] = value
                self.assert_rejected(inputs(raw), R.INACTIVE_OR_SUSPENDED)
        for status in ('inactive','expired','liquid','closed','suspended'):
            with self.subTest(status=status):
                raw = raw_fixture()
                raw['plan']['payload']['result']['userPackageDetail']['status'] = status
                # Even an erroneous active-status interpretation cannot permit known terminal labels.
                raw['semantics']['active_plan_status'] = status
                self.assert_rejected(inputs(raw), R.INACTIVE_OR_SUSPENDED)

    def test_missing_incomplete_and_partial_payloads_are_not_zero_or_complete(self):
        evidence = inputs()
        for name in ('accounts','plan','balances'):
            for bad in (None, replace(getattr(evidence,name),complete=False),
                        replace(getattr(evidence,name),payload={'success':False,'result':{}})):
                with self.subTest(response=name,bad=bad):
                    self.assert_rejected(replace(evidence,**{name:bad}))
        for change in ('missing-result','partial-page','duplicate-account','missing-status'):
            raw = raw_fixture()
            if change == 'missing-result':del raw['balances']['payload']['result']
            elif change == 'partial-page':raw['accounts']['payload']['result_info']['total_count'] = 2
            elif change == 'duplicate-account':
                raw['accounts']['payload']['result'] *= 2
                raw['accounts']['payload']['result_info'].update(count=2,total_count=2)
            else:del raw['plan']['payload']['result']['userPackageDetail']['status']
            with self.subTest(change=change):self.assert_rejected(inputs(raw))
        for invalid in (None,{},'invalid'):
            with self.subTest(container=invalid):self.assert_rejected(invalid,R.INVALID_INPUT)

    def test_balance_completeness_consistency_and_zero_available_units(self):
        for change in ('missing-quote','missing-available','unbalanced','foreign-currency'):
            raw = raw_fixture()
            balances = raw['balances']['payload']['result']
            if change == 'missing-quote':del balances['TMN']
            elif change == 'missing-available':del balances['TMN']['available']
            elif change == 'unbalanced':balances['TMN']['available'] = '3000'
            else:balances['IRR'] = dict(balances['TMN'],symbol='IRR')
            with self.subTest(change=change):self.assert_rejected(inputs(raw))
        raw = raw_fixture()
        raw['balances']['payload']['result']['TMN'].update(freeze='3000',available='0')
        result = credit.assess_credit_account(inputs(raw))
        self.assertTrue(result.snapshot_valid)
        self.assertEqual(result.snapshot.available_quote,D(0))
        self.assertTrue(result.blocks_new_entries)

    def test_wire_amounts_reject_float_bool_nonfinite_negative_and_resource_excess(self):
        for amount in (1.0,True,None,'NaN','Infinity','-1',' 1','1e999','1'*101):
            with self.subTest(amount=amount):
                raw = raw_fixture()
                raw['balances']['payload']['result']['TMN']['available'] = amount
                self.assert_rejected(inputs(raw))

    def test_expiry_cutoff_and_utc_time_evidence(self):
        for expires in ('1970-01-21T20:00:00Z','1970-01-21T19:59:59Z'):
            raw = raw_fixture()
            raw['plan']['payload']['result']['userPackageDetail']['expireAt'] = expires
            with self.subTest(expiry=expires):self.assert_rejected(inputs(raw),R.EXPIRED_PLAN)
        for expires in ('invalid','1970-01-22T20:00:00','1970-01-22T20:00:00+01:00',
                        '1970-01-22T20:00:00.1Z'):
            raw = raw_fixture()
            raw['plan']['payload']['result']['userPackageDetail']['expireAt'] = expires
            with self.subTest(expiry=expires):self.assert_rejected(inputs(raw),R.INVALID_TIMESTAMP)
        raw = raw_fixture()
        raw['plan']['payload']['result']['userPackageDetail']['createdAt'] = '1970-01-22T00:00:00Z'
        self.assert_rejected(inputs(raw),R.INVALID_TIMESTAMP)

    def test_per_response_freshness_inclusive_boundaries_and_explicit_cutoff(self):
        evidence = inputs()
        self.assertTrue(credit.assess_credit_account(replace(evidence,max_age_seconds=10)).snapshot_valid)
        self.assert_rejected(replace(evidence,max_age_seconds=9),R.STALE_EVIDENCE)
        for name in ('accounts','plan','balances'):
            for observed,reason in ((evidence.as_of+1,R.FUTURE_EVIDENCE),
                                    (evidence.as_of-31,R.STALE_EVIDENCE), (None,R.INVALID_TIMESTAMP)):
                with self.subTest(response=name,observed=observed):
                    self.assert_rejected(replace(evidence,**{name:replace(getattr(evidence,name),observed_at=observed)}),reason)
        same = replace(evidence,max_age_seconds=0,**{
            name:replace(getattr(evidence,name),observed_at=evidence.as_of)
            for name in ('accounts','plan','balances')})
        self.assertTrue(credit.assess_credit_account(same).snapshot_valid)
        for changes in ({'as_of':True},{'max_age_seconds':None},{'max_age_seconds':-1}):
            with self.subTest(changes=changes):self.assert_rejected(replace(evidence,**changes),R.INVALID_TIMESTAMP)

    def test_ambiguous_currency_units_and_threshold_debt_meanings_block(self):
        evidence = inputs()
        for changes in ({'fee_currency':'USDT'},{'debt_currency':'IRR'},
                        {'threshold_currency':None},{'threshold_basis':'PRICE'},
                        {'debt_basis':'LOAN_EQUALS_DEBT'}, {'active_plan_status':None},
                        {'balance_units':(('TMN','IRR'),('USDT','USDT'))}):
            with self.subTest(semantics=changes):
                self.assert_rejected(replace(evidence,semantics=replace(evidence.semantics,**changes)),
                                     R.AMBIGUOUS_UNITS_OR_SEMANTICS)
        for changes in ({'currency':'IRR'},{'currency':'tmn'}):
            with self.subTest(binding=changes):
                self.assert_rejected(replace(evidence,binding=replace(evidence.binding,**changes)),
                                     R.AMBIGUOUS_UNITS_OR_SEMANTICS)
        raw = raw_fixture()
        raw['plan']['payload']['result']['packageDetail']['currency'] = 'USDT'
        self.assert_rejected(inputs(raw),R.AMBIGUOUS_UNITS_OR_SEMANTICS)

    def test_collateral_debt_fee_and_threshold_claims_are_required_and_coherent(self):
        for section,name,value in (
            ('packageDetail','assurance','0'), ('packageDetail','loan','0'),
            ('packageDetail','dailyFeeAfterDiscount','9'),
            ('userPackageDetail','warningLimit','2000'), ('userPackageDetail','liquidityLimit','0'),
            ('userPackageDetail','finalPayback',None), ('userPackageDetail','finalPayableFeeAmount',None),
        ):
            with self.subTest(field=name):
                raw = raw_fixture()
                body = raw['plan']['payload']['result'][section]
                if value is None:del body[name]
                else:body[name] = value
                self.assert_rejected(inputs(raw))

    def test_every_response_scope_must_match_explicit_account(self):
        evidence = inputs()
        for name in ('accounts','plan','balances'):
            for scope in ('main-account','another-credit-account',None):
                with self.subTest(response=name,scope=scope):
                    self.assert_rejected(replace(evidence,**{
                        name:replace(getattr(evidence,name),sub_account_client_id=scope)}),R.IDENTITY_MISMATCH)

    def test_immutable_deterministic_no_execution_or_credential_scope_assumption(self):
        evidence = inputs()
        before = deepcopy(evidence)
        with patch.object(gate,'evaluate_entry',side_effect=AssertionError('No Risk activation')) as check, \
                patch.object(ReplayGateway,'submit',side_effect=AssertionError('No execution')) as submit, \
                patch('urllib.request.urlopen',side_effect=AssertionError('Offline only')) as network:
            result = credit.assess_credit_account(evidence)
            with localcontext() as ctx:
                ctx.prec = 1;ctx.rounding = ROUND_UP;ctx.traps[Inexact] = True
                self.assertEqual(credit.assess_credit_account(evidence),result)
            check.assert_not_called();submit.assert_not_called();network.assert_not_called()
        self.assertEqual(evidence,before)
        with self.assertRaises(FrozenInstanceError):result.snapshot.balances[0].available = D(1)
        with self.assertRaises(FrozenInstanceError):result.snapshot.risk_equity = D(3000)
        with self.assertRaises((TypeError,ValueError)):replace(result,executable_ready=True)
        self.assertFalse(result.credential_scope_verified or result.executable_ready)
        self.assertIs(result.executable_action,Signal.NO_TRADE)
        self.assertNotIn('api_key',{f.name for f in fields(credit.CreditEvidenceInputs)})
        evidence.balances.payload['result']['TMN']['available'] = '9999'
        self.assertEqual(result.snapshot.available_quote,D(2750))
        self.assertTrue(result.blocks_new_entries)


if __name__ == '__main__':
    unittest.main()
