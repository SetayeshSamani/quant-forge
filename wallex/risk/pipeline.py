"""Read-only TP-1 Strategy/Risk composition; never full risk or execution approval."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from wallex.strategy.engine import Signal
from wallex.strategy.frame_adapter import FrameDiagnostics, assess_market_frame

from .loss_guards import LossGuardAssessment, LossGuardState, assess_loss_guards
from .portfolio_guards import (
    PortfolioGuardAssessment, PortfolioGuardState, assess_portfolio_entry,
)
from .sizing import (
    RiskProfile, SizingAssessment, SizingInputs, assess_position_size, policy_for,
)


@dataclass(frozen=True)
class PipelineInputs:
    """Explicit local evidence; None never selects accounts, prices or policies.

    frame follows the adapter's passive attribute interface. Obtain a stable
    snapshot upstream; freezing this record does not freeze a foreign object.
    """

    frame: object = None
    pe: Decimal | None = None
    worst_case_extra_loss_per_unit: Decimal | None = None
    equity: Decimal | None = None
    free_quote: Decimal | None = None
    quote_asset: str | None = None
    account_id: str | None = None
    profile: RiskProfile | None = None
    loss_state: LossGuardState | None = None
    portfolio_state: PortfolioGuardState | None = None


class PipelineReason(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_SIZING_INPUT = "MISSING_SIZING_INPUT"
    MISSING_COST_EVIDENCE = "MISSING_COST_EVIDENCE"
    INVALID_SIZING_INPUT = "INVALID_SIZING_INPUT"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INVALID_PROFILE = "INVALID_PROFILE"
    FRAME_REJECTED = "FRAME_REJECTED"
    TECHNICAL_REJECTED = "TECHNICAL_REJECTED"
    STOP_UNAVAILABLE_OR_INVALID = "STOP_UNAVAILABLE_OR_INVALID"
    LOSS_GUARD_REJECTED = "LOSS_GUARD_REJECTED"
    LOSS_GUARD_BLOCKED = "LOSS_GUARD_BLOCKED"
    PORTFOLIO_GUARD_REJECTED = "PORTFOLIO_GUARD_REJECTED"
    PORTFOLIO_GUARD_BLOCKED = "PORTFOLIO_GUARD_BLOCKED"
    CUTOFF_MISMATCH = "CUTOFF_MISMATCH"
    QUOTE_ASSET_MISMATCH = "QUOTE_ASSET_MISMATCH"
    ACCOUNT_ID_MISMATCH = "ACCOUNT_ID_MISMATCH"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    EQUITY_MISMATCH = "EQUITY_MISMATCH"
    COMPONENT_FAILURE = "COMPONENT_FAILURE"
    SIZING_REJECTED = "SIZING_REJECTED"
    LOCAL_DIAGNOSTIC_CONDITIONS_MET = "LOCAL_DIAGNOSTIC_CONDITIONS_MET"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    MARKET_EVIDENCE_UNAPPROVED = "MARKET_EVIDENCE_UNAPPROVED"
    PE_COST_PROVENANCE_UNAPPROVED = "PE_COST_PROVENANCE_UNAPPROVED"
    LOSS_ACCOUNT_PROVENANCE_UNRESOLVED = "LOSS_ACCOUNT_PROVENANCE_UNRESOLVED"
    ATOMIC_ADMISSION_UNAVAILABLE = "ATOMIC_ADMISSION_UNAVAILABLE"


class PipelineStage(str, Enum):
    FRAME = "FRAME"
    LOSS_GUARD = "LOSS_GUARD"
    PORTFOLIO_GUARD = "PORTFOLIO_GUARD"
    SIZING = "SIZING"


@dataclass(frozen=True)
class SkippedStage:
    stage: PipelineStage
    reasons: tuple[PipelineReason, ...]


@dataclass(frozen=True)
class PipelineAssessment:
    """Actual component diagnostics, never a RiskDecision or admission token.

    Quantity exists only inside a valid optional SizingAssessment. Foreign frame
    objects are not retained. Limitations remain even when local conditions pass.
    """

    reasons: tuple[PipelineReason, ...]
    coherence_valid: bool = False
    local_conditions_satisfied: bool = False
    frame: FrameDiagnostics | None = None
    loss: LossGuardAssessment | None = None
    portfolio: PortfolioGuardAssessment | None = None
    sizing: SizingAssessment | None = None
    invalid_fields: tuple[str, ...] = ()
    skipped_stages: tuple[SkippedStage, ...] = ()
    failed_stages: tuple[PipelineStage, ...] = ()
    executable_ready: bool = field(default=False, init=False)
    executable_action: Signal = field(default=Signal.NO_TRADE, init=False)
    limitations: tuple[PipelineReason, ...] = field(default=(
        PipelineReason.DIAGNOSTIC_ONLY,
        PipelineReason.MARKET_EVIDENCE_UNAPPROVED,
        PipelineReason.PE_COST_PROVENANCE_UNAPPROVED,
        PipelineReason.LOSS_ACCOUNT_PROVENANCE_UNRESOLVED,
        PipelineReason.ATOMIC_ADMISSION_UNAVAILABLE,
    ), init=False)


def _identity(value: object, *, quote: bool = False) -> bool:
    return (type(value) is str and bool(value) and value.strip() == value and len(value) <= 200
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and not (quote and any(c.isspace() for c in value)))


def _input_issues(inputs: PipelineInputs) -> tuple[list[PipelineReason], list[str]]:
    reasons, fields = [], []
    for name in ("pe", "equity", "free_quote", "worst_case_extra_loss_per_unit"):
        value = getattr(inputs, name)
        if value is None:
            reasons.append(PipelineReason.MISSING_COST_EVIDENCE if name == "worst_case_extra_loss_per_unit"
                           else PipelineReason.MISSING_SIZING_INPUT)
            fields.append(name)
        elif (not isinstance(value, Decimal) or not value.is_finite()
              or value < 0 or (name in ("pe", "equity") and value == 0)):
            reasons.append(PipelineReason.INVALID_SIZING_INPUT)
            fields.append(name)
    for name in ("account_id", "quote_asset"):
        if not _identity(getattr(inputs, name), quote=name == "quote_asset"):
            reasons.append(PipelineReason.INVALID_IDENTITY)
            fields.append(name)
    if type(inputs.profile) is not RiskProfile:
        reasons.append(PipelineReason.INVALID_PROFILE)
        fields.append("profile")
    return reasons, fields


def _component(stage, operation, expected_type, failed):
    try:
        value = operation()
        if type(value) is not expected_type:
            raise TypeError("Unexpected component result")
        return value
    except Exception:
        # A raised component has no result to preserve; never fabricate one or
        # expose arbitrary foreign exception text. Other independent stages run.
        failed.append(stage)
        return None


def assess_risk_pipeline(inputs: PipelineInputs | None = None) -> PipelineAssessment:
    """Collect independent diagnostics, validate coherence, then optionally size.

    Market incompleteness/provisional provenance never become execution approval.
    Local limits and a theoretical cap do not prove account correlation or atomic
    admission. No Gate, wall clock, persistence, market request or order is invoked.
    """
    reason = PipelineReason
    stage = PipelineStage
    if type(inputs) is not PipelineInputs:
        return PipelineAssessment(
            (reason.INVALID_INPUT,),
            skipped_stages=tuple(SkippedStage(s, (reason.INVALID_INPUT,)) for s in stage),
        )
    failed = []
    frame = _component(stage.FRAME, lambda: assess_market_frame(inputs.frame, pe=inputs.pe),
                       FrameDiagnostics, failed)
    loss = _component(stage.LOSS_GUARD, lambda: assess_loss_guards(inputs.loss_state),
                      LossGuardAssessment, failed)
    portfolio = _component(stage.PORTFOLIO_GUARD,
                           lambda: assess_portfolio_entry(inputs.portfolio_state),
                           PortfolioGuardAssessment, failed)
    issues, invalid_fields = _input_issues(inputs)
    frame_valid = frame is not None and frame.frame_valid and frame.metadata is not None
    loss_valid = loss is not None and loss.calculation_valid and loss.state is not None
    portfolio_valid = portfolio is not None and portfolio.snapshot_valid and portfolio.state is not None
    for valid, code in ((frame_valid, reason.FRAME_REJECTED),
                        (loss_valid, reason.LOSS_GUARD_REJECTED),
                        (portfolio_valid, reason.PORTFOLIO_GUARD_REJECTED)):
        if not valid:
            issues.append(code)
    if not issues:
        if not frame.metadata.as_of == loss.state.as_of == portfolio.state.as_of:
            issues.append(reason.CUTOFF_MISMATCH)
        if not inputs.quote_asset == frame.metadata.quote_asset == loss.state.quote_asset == portfolio.state.quote_asset:
            issues.append(reason.QUOTE_ASSET_MISMATCH)
        if inputs.account_id != portfolio.state.account_id:
            issues.append(reason.ACCOUNT_ID_MISMATCH)
        if inputs.profile is not loss.state.policy.profile:
            issues.append(reason.PROFILE_MISMATCH)
        if inputs.equity != loss.state.current_equity:
            issues.append(reason.EQUITY_MISMATCH)
    coherence_valid = not issues
    if frame_valid:
        if not frame.technical_eligible:
            issues.append(reason.TECHNICAL_REJECTED)
        if frame.stop is None or not frame.stop.valid:
            issues.append(reason.STOP_UNAVAILABLE_OR_INVALID)
    if loss_valid and loss.blocks_new_entries:
        issues.append(reason.LOSS_GUARD_BLOCKED)
    if portfolio_valid and portfolio.blocks_new_entries:
        issues.append(reason.PORTFOLIO_GUARD_BLOCKED)
    if failed:
        issues.append(reason.COMPONENT_FAILURE)
    sizing = None
    skipped = ()
    if issues:
        skipped = (SkippedStage(stage.SIZING, tuple(dict.fromkeys(issues))),)
    else:
        def calculate_size():
            policy = policy_for(inputs.profile)  # explicit caller-selected profile only
            return assess_position_size(SizingInputs(
                equity=inputs.equity, free_quote=inputs.free_quote, quote_asset=inputs.quote_asset,
                pe=inputs.pe, initial_stop=frame.stop.initial_stop,
                worst_case_extra_loss_per_unit=inputs.worst_case_extra_loss_per_unit,
                risk_fraction=policy.risk_fraction, max_position_fraction=policy.max_position_fraction,
            ))
        sizing = _component(stage.SIZING, calculate_size, SizingAssessment, failed)
        if sizing is None:
            issues.append(reason.COMPONENT_FAILURE)
        elif not sizing.calculation_valid:
            issues.append(reason.SIZING_REJECTED)
    local_pass = sizing is not None and sizing.calculation_valid and not issues
    return PipelineAssessment(
        reasons=tuple(dict.fromkeys(issues)) or (reason.LOCAL_DIAGNOSTIC_CONDITIONS_MET,),
        coherence_valid=coherence_valid, local_conditions_satisfied=local_pass,
        frame=frame, loss=loss, portfolio=portfolio, sizing=sizing,
        invalid_fields=tuple(invalid_fields), skipped_stages=skipped, failed_stages=tuple(failed),
    )
