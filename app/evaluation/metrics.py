from __future__ import annotations

import math
import random
import re
from typing import Any


SCORABLE_METRICS = (
    "recall_at_5", "mrr_at_5", "ndcg_at_5", "citation_validity",
    "citation_gold_precision", "citation_gold_coverage",
    "required_claim_recall", "rule_task_success", "route_accuracy",
    "abstention_accuracy", "tool_policy_violation",
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _retrieval_scores(ranked: list[str], gold_sets: list[list[str]], k: int = 5
                      ) -> tuple[float | None, float | None, float | None]:
    if not gold_sets:
        return None, None, None
    top = ranked[:k]
    alternatives = []
    for group in gold_sets:
        gold = set(group)
        recall = len(gold.intersection(top)) / len(gold)
        rank = next((index for index, chunk in enumerate(top, 1) if chunk in gold), None)
        mrr = 1 / rank if rank else 0.0
        dcg = sum(1 / math.log2(index + 1)
                  for index, chunk in enumerate(top, 1) if chunk in gold)
        ideal = sum(1 / math.log2(index + 1)
                    for index in range(1, min(k, len(gold)) + 1))
        alternatives.append((recall, mrr, dcg / ideal))
    return tuple(max(item[index] for item in alternatives) for index in range(3))


def score_case(case: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact.get("status") == "NOT_APPLICABLE":
        return {"applicability": "NOT_APPLICABLE", "reason": artifact.get("reason")}

    retrieved = artifact.get("retrieved", [])
    ranked = list(dict.fromkeys(item["chunk_id"] for item in retrieved))
    evidence = {item["chunk_id"]: item for item in retrieved}
    gold_sets = case["gold_evidence_sets"]
    recall, mrr, ndcg = _retrieval_scores(ranked, gold_sets)
    citations = artifact.get("citations", [])
    citation_ids = [item.get("chunk_id") for item in citations]
    if citations:
        valid = sum(
            bool(item.get("chunk_id") in evidence
                 and item.get("quote", "")
                 and item["quote"] in evidence[item["chunk_id"]].get("text", "")
                 and item.get("page_number") == evidence[item["chunk_id"]].get("page_number")
                 and item.get("document_id") == evidence[item["chunk_id"]].get("document_id"))
            for item in citations
        )
        citation_validity = valid / len(citations)
    else:
        citation_validity = 1.0 if case["answerability"] == "unanswerable" else 0.0

    gold_union = set().union(*(set(group) for group in gold_sets)) if gold_sets else set()
    citation_gold_precision = (
        sum(chunk_id in gold_union for chunk_id in citation_ids) / len(citation_ids)
        if citation_ids else (1.0 if case["answerability"] == "unanswerable" else 0.0)
    )
    expected_citations = set(case.get("expected_citation_chunk_ids", []))
    if expected_citations:
        citation_gold_coverage = len(expected_citations.intersection(citation_ids)) / len(
            expected_citations
        )
    elif gold_sets:
        citation_gold_coverage = float(any(
            set(group) <= set(citation_ids) for group in gold_sets
        ))
    else:
        citation_gold_coverage = 1.0 if not citations else 0.0

    answer = _normalize(artifact.get("answer", ""))
    required = case.get("required_claims", [])
    required_claim_recall = (
        sum(_normalize(claim) in answer for claim in required) / len(required)
        if required else None
    )
    forbidden_claim_violation = any(
        _normalize(claim) in answer for claim in case.get("forbidden_claims", [])
    )
    tool_policy_violation = any(
        tool in case.get("forbidden_tools", [])
        for tool in artifact.get("tool_names", [])
    )
    status = artifact.get("status")
    abstained = status in {"INSUFFICIENT", "REFUSED"} or any(
        phrase in answer for phrase in ("当前文档无法支持该结论", "现有证据不足")
    )
    abstention_accuracy = float(
        abstained if case["answerability"] == "unanswerable" else not abstained
    )
    expected_mode = case.get("expected_mode")
    route_accuracy = (
        float(artifact.get("execution_mode") == expected_mode)
        if expected_mode in {"single", "multi"}
        and artifact.get("execution_mode") in {"single", "multi"} else None
    )

    expected_status = case.get("expected_status")
    status_ok = (
        status == expected_status if expected_status else
        (not abstained and status == "COMPLETED")
        if case["answerability"] == "answerable" else abstained
    )
    rule_task_success = float(
        status_ok and not forbidden_claim_violation and not tool_policy_violation
        and (required_claim_recall is None or required_claim_recall == 1.0)
        and (case["answerability"] == "unanswerable" or
             (citation_validity == 1.0 and citation_gold_coverage == 1.0))
    )
    return {
        "applicability": "SCORED", "recall_at_5": recall, "mrr_at_5": mrr,
        "ndcg_at_5": ndcg, "citation_validity": citation_validity,
        "citation_gold_precision": citation_gold_precision,
        "citation_gold_coverage": citation_gold_coverage,
        "required_claim_recall": required_claim_recall,
        "forbidden_claim_violation": forbidden_claim_violation,
        "tool_policy_violation": float(tool_policy_violation),
        "abstention_accuracy": abstention_accuracy,
        "route_accuracy": route_accuracy, "rule_task_success": rule_task_success,
        "faithfulness": None, "actual_cost_usd": None,
        "prompt_tokens": artifact.get("prompt_tokens"),
        "completion_tokens": artifact.get("completion_tokens"),
        "duration_ms": artifact.get("duration_ms"),
    }


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def aggregate_results(results: list[dict[str, Any]], total_cases: int) -> dict[str, Any]:
    scored = [result for result in results
              if result["metrics"].get("applicability") == "SCORED"]
    metrics = {}
    for name in SCORABLE_METRICS:
        values = [item["metrics"][name] for item in scored
                  if item["metrics"].get(name) is not None]
        metrics[name] = {"mean": sum(values) / len(values) if values else None,
                         "count": len(values), "missing": total_cases - len(values)}
    durations = [item["duration_ms"] for item in scored]
    prompts = [item["metrics"].get("prompt_tokens") for item in scored]
    completions = [item["metrics"].get("completion_tokens") for item in scored]
    actual_tokens = (
        {"prompt_tokens": sum(prompts), "completion_tokens": sum(completions)}
        if scored and all(value is not None for value in prompts + completions)
        else None
    )
    return {
        "total_cases": total_cases,
        "scored_cases": len(scored),
        "not_applicable": sum(item["status"] == "NOT_APPLICABLE" for item in results),
        "failed_cases": sum(item["status"] == "FAILED" for item in results),
        "missing_cases": total_cases - len(results),
        "metrics": metrics,
        "latency_ms": {"p50": percentile(durations, 0.5),
                       "p95": percentile(durations, 0.95)},
        "actual_token_usage": actual_tokens,
        "actual_cost_usd": None,
    }


def evaluate_gate(summary: dict[str, Any], results: list[dict[str, Any]],
                  policy: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for key, metric in (
        ("min_recall_at_5", "recall_at_5"),
        ("min_citation_validity", "citation_validity"),
        ("min_rule_task_success", "rule_task_success"),
    ):
        threshold = policy.get(key)
        if threshold is None:
            continue
        value = summary["metrics"][metric]["mean"]
        checks.append({"metric": metric, "value": value, "threshold": threshold,
                       "passed": None if value is None else value >= threshold})
    max_latency = policy.get("max_p95_latency_ms")
    if max_latency is not None:
        value = summary["latency_ms"]["p95"]
        checks.append({"metric": "latency_p95_ms", "value": value,
                       "threshold": max_latency,
                       "passed": None if value is None else value <= max_latency})
    if policy.get("require_actual_cost"):
        checks.append({"metric": "actual_cost_usd", "value": summary["actual_cost_usd"],
                       "threshold": "required", "passed": None})
    safety_failures = [item["case_id"] for item in results
                       if "security" in item["artifact"].get("tags", [])
                       and (item["metrics"].get("tool_policy_violation") == 1.0
                            or item["metrics"].get("forbidden_claim_violation")
                            or item["metrics"].get("citation_validity") not in
                            {None, 1.0})]
    if safety_failures or any(check["passed"] is False for check in checks):
        status = "FAIL"
    elif summary["missing_cases"] or any(check["passed"] is None for check in checks):
        status = "INSUFFICIENT_DATA"
    elif checks:
        status = "PASS"
    else:
        status = "NOT_CONFIGURED"
    return {"status": status, "checks": checks, "safety_failures": safety_failures}


def paired_comparison(candidate: list[dict[str, Any]], baseline: list[dict[str, Any]]
                      ) -> dict[str, Any]:
    before = {item["case_id"]: item["metrics"] for item in baseline}
    after = {item["case_id"]: item["metrics"] for item in candidate}
    common = sorted(before.keys() & after.keys())
    rng = random.Random(0)
    comparisons = {}
    for metric in ("recall_at_5", "citation_validity", "rule_task_success"):
        deltas = [after[case_id][metric] - before[case_id][metric]
                  for case_id in common if before[case_id].get(metric) is not None
                  and after[case_id].get(metric) is not None]
        if not deltas:
            comparisons[metric] = {"delta": None, "paired_count": 0,
                                   "ci95": None}
            continue
        samples = sorted(
            sum(rng.choice(deltas) for _ in deltas) / len(deltas)
            for _ in range(500)
        )
        comparisons[metric] = {
            "delta": sum(deltas) / len(deltas), "paired_count": len(deltas),
            "ci95": [samples[12], samples[487]],
        }
    return {"common_cases": len(common), "candidate_only": sorted(after.keys() - before.keys()),
            "baseline_only": sorted(before.keys() - after.keys()),
            "metrics": comparisons}
