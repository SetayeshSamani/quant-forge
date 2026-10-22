"""Pure side-by-side credit/risk diagnostics; no approved credit-risk policy."""

from dataclasses import dataclass, field

from wallex.infra.credit_account import CreditAssessment, CreditEvidenceInputs, assess_credit_account
from wallex.strategy.engine import Signal
from .pipeline import PipelineAssessment, PipelineInputs, assess_risk_pipeline


@dataclass(frozen=True)
class CreditRiskDiagnostics:
    """Independent local results, never combined account/risk authorization."""

    credit: CreditAssessment
    risk: PipelineAssessment
    blocks_new_entries: bool = field(default=True, init=False)
    executable_ready: bool = field(default=False, init=False)
    executable_action: Signal = field(default=Signal.NO_TRADE, init=False)
    limitations: tuple[str, ...] = field(default=(
        "INDEPENDENT_DIAGNOSTICS_ONLY", "CREDIT_RISK_CORRELATION_UNVERIFIED",
    ), init=False)

    @property
    def credit_evidence_ready(self) -> bool:
        """Local CREDIT-02 checks only, not verified auth, equity or risk readiness."""
        return self.credit.snapshot_valid


def assess_credit_risk_diagnostics(
    credit_inputs: CreditEvidenceInputs | None = None,
    risk_inputs: PipelineInputs | None = None,
) -> CreditRiskDiagnostics:
    """Preserve both assessments/reasons without deriving or substituting inputs.

    Missing plan evidence fails existing credit validation. Risk diagnostics stay
    independent; account/cutoff correlation and credit-risk semantics are unapproved.
    No balances are mapped to equity/funds, and no combined approval is exposed.
    """
    return CreditRiskDiagnostics(assess_credit_account(credit_inputs), assess_risk_pipeline(risk_inputs))
