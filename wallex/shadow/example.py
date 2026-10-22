"""Historical/synthetic shadow observation only; no order transport exists."""

import argparse
import json
from pathlib import Path

from wallex.backtest.example import example_plan, synthetic_decision
from wallex.market.evaluation import EvidenceKind, json_value
from .runner import run_shadow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="NEW output directory")
    args = parser.parse_args()
    result = run_shadow(example_plan(), synthetic_decision,
                        strategy_version="synthetic-two-actions-v1",
                        evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)
    if args.output is not None:
        result.export(args.output)
    print(json.dumps(json_value({"mode": "shadow", "evidence_kind": "synthetic_fixture",
                                "run_id": result.run_id, "summary": result.summary}), indent=2))


if __name__ == "__main__":
    main()
