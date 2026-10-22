"""Replay-backed shadow decision observation. No executor, fills, or holdings."""

from dataclasses import dataclass

from wallex.market.evaluation import (
    DecisionProvider, EvaluationRecord, EvidenceKind, Journal, RunEvent,
    evaluate_step, run_metadata, write_artifacts,
)
from wallex.market.replay import ReplayPlan


@dataclass(frozen=True)
class ShadowSummary:
    step_count: int
    decision_count: int
    error_count: int


@dataclass(frozen=True)
class ShadowResult:
    run_id: str
    metadata_json: str
    summary: ShadowSummary
    evaluations: tuple[EvaluationRecord, ...]
    events: tuple[RunEvent, ...]

    def export(self, directory):
        return write_artifacts(directory, metadata_json=self.metadata_json,
                               summary=self.summary, events=self.events,
                               tables=(("evaluations.jsonl", self.evaluations),))


def run_shadow(plan: ReplayPlan, provider: DecisionProvider, *, strategy_version: str,
               evidence_kind: EvidenceKind) -> ShadowResult:
    """Audit every decision/error on an explicit historical/synthetic replay window.

    This runner is ready for an agreed future market/strategy adapter. It does
    not fetch a live feed and never interprets an observed BUY as authorization.
    """
    if not callable(provider):
        raise ValueError("callable provider required")
    metadata = run_metadata(plan, mode="shadow", evidence_kind=evidence_kind,
                            strategy_version=strategy_version)
    journal = Journal(metadata)
    evaluations = []
    journal.add(plan.start_as_of, "run_started", {"strategy_version": strategy_version})
    for step in plan.steps():
        record = evaluate_step(step, provider)
        evaluations.append(record)
        journal.add(step.as_of, "evaluation", record)
    summary = ShadowSummary(len(evaluations), sum(e.decision is not None for e in evaluations),
                            sum(e.error_code is not None for e in evaluations))
    journal.add(plan.end_as_of, "run_completed", summary)
    return ShadowResult(journal.run_id, metadata, summary, tuple(evaluations), tuple(journal.events))
