"""Compare offline decision traces with matching provenance, never actual receipts."""

from dataclasses import dataclass
import json

from wallex.market.evaluation import canonical_json


@dataclass(frozen=True)
class DecisionDifference:
    as_of: int
    left_json: str | None
    right_json: str | None


@dataclass(frozen=True)
class ComparisonReport:
    matches: bool
    compared_steps: int
    differences: tuple[DecisionDifference, ...]


def compare_runs(left, right) -> ComparisonReport:
    """Require the same source/settings/provider version; ignore mode/cost assumptions.

    This compares decisions and input quality, not hypothetical fills against
    exchange executions. It can also detect drift between repeated same-mode runs.
    """
    provenance = []
    for result in (left, right):
        metadata = json.loads(result.metadata_json)
        metadata.pop("mode")
        metadata.pop("assumptions")
        provenance.append(metadata)
    if provenance[0] != provenance[1]:
        raise ValueError("decision comparison requires matching sources, window, assets and strategy version")
    traces = []
    for result in (left, right):
        trace = {record.as_of: canonical_json(record) for record in result.evaluations}
        if len(trace) != len(result.evaluations):
            raise ValueError("duplicate decision timestamps cannot be compared")
        traces.append(trace)
    times = sorted(traces[0].keys() | traces[1].keys())
    differences = tuple(DecisionDifference(t, traces[0].get(t), traces[1].get(t))
                        for t in times if traces[0].get(t) != traces[1].get(t))
    return ComparisonReport(not differences, len(times), differences)
