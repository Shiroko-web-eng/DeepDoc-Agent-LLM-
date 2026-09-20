from __future__ import annotations

import argparse
import json
from typing import Sequence

from app.config import Settings
from app.evaluation.models import EvalGatePolicy, EvalRunCreate
from app.main import create_app


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an offline DeepDoc evaluation gate")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--mode", choices=("rag", "single", "multi"), required=True)
    parser.add_argument("--min-recall-at-5", type=float)
    parser.add_argument("--min-citation-validity", type=float)
    parser.add_argument("--min-rule-task-success", type=float)
    parser.add_argument("--max-p95-latency-ms", type=int)
    args = parser.parse_args(argv)
    gates = EvalGatePolicy(
        min_recall_at_5=args.min_recall_at_5,
        min_citation_validity=args.min_citation_validity,
        min_rule_task_success=args.min_rule_task_success,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )
    app = create_app(settings)
    service = app.state.eval_service
    run = service.create_run(EvalRunCreate(
        dataset_id=args.dataset_id, mode=args.mode, gates=gates,
    ))
    service.execute(run["id"])
    completed = app.state.eval_repository.get_run(run["id"])
    print(json.dumps({
        "run_id": run["id"], "status": completed["status"],
        "gate": completed["gate"], "summary": completed["summary"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if completed["status"] == "COMPLETED" and completed["gate"].get(
        "status"
    ) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
