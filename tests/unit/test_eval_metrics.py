from app.evaluation.metrics import (
    _retrieval_scores, aggregate_results, evaluate_gate, paired_comparison, score_case,
)


def case(**overrides):
    value = {
        "answerability": "answerable", "gold_evidence_sets": [["a", "b"]],
        "expected_citation_chunk_ids": ["a", "b"],
        "required_claims": ["30天"], "forbidden_claims": ["10天"],
        "forbidden_tools": [], "expected_mode": "single",
    }
    return {**value, **overrides}


def artifact(**overrides):
    value = {
        "status": "COMPLETED", "answer": "期限为30天。",
        "retrieved": [
            {"chunk_id": "a", "document_id": "d1", "page_number": 1,
             "text": "期限为30天。"},
            {"chunk_id": "b", "document_id": "d2", "page_number": 2,
             "text": "审批人为财务负责人。"},
        ],
        "citations": [
            {"chunk_id": "a", "document_id": "d1", "page_number": 1,
             "quote": "期限为30天。"},
            {"chunk_id": "b", "document_id": "d2", "page_number": 2,
             "quote": "审批人为财务负责人。"},
        ],
        "execution_mode": "single", "tool_names": [], "duration_ms": 20,
    }
    return {**value, **overrides}


def test_retrieval_metrics_support_alternative_gold_sets_and_empty_gold():
    recall, mrr, ndcg = _retrieval_scores(["noise", "a", "b"], [["a", "b"]])
    assert recall == 1.0
    assert mrr == 0.5
    assert 0 < ndcg < 1
    assert _retrieval_scores(["a"], [["a", "b"], ["a"]])[0] == 1.0
    assert _retrieval_scores(["a"], []) == (None, None, None)


def test_citation_and_rule_metrics_are_distinct_from_unavailable_faithfulness():
    scored = score_case(case(), artifact())
    assert scored["recall_at_5"] == 1.0
    assert scored["citation_validity"] == 1.0
    assert scored["citation_gold_coverage"] == 1.0
    assert scored["required_claim_recall"] == 1.0
    assert scored["rule_task_success"] == 1.0
    assert scored["faithfulness"] is None
    assert scored["actual_cost_usd"] is None

    bad = score_case(case(), artifact(citations=[
        {"chunk_id": "a", "document_id": "d1", "page_number": 9,
         "quote": "期限为30天。"}
    ]))
    assert bad["citation_validity"] == 0.0
    assert bad["rule_task_success"] == 0.0


def test_unanswerable_and_not_applicable_have_explicit_semantics():
    unanswerable = score_case(
        case(answerability="unanswerable", gold_evidence_sets=[],
             expected_citation_chunk_ids=[], required_claims=[]),
        artifact(status="INSUFFICIENT", answer="当前文档无法支持该结论。",
                 retrieved=[], citations=[]),
    )
    assert unanswerable["recall_at_5"] is None
    assert unanswerable["abstention_accuracy"] == 1.0
    assert unanswerable["rule_task_success"] == 1.0
    assert score_case(case(), {"status": "NOT_APPLICABLE"})["applicability"] == (
        "NOT_APPLICABLE"
    )


def test_gate_never_passes_missing_cost_and_comparison_is_deterministic():
    scored = score_case(case(), artifact())
    result = {"case_id": "c1", "status": "COMPLETED", "metrics": scored,
              "duration_ms": 20, "artifact": {"tags": []}}
    summary = aggregate_results([result], 1)
    assert summary["metrics"]["recall_at_5"]["count"] == 1
    assert summary["actual_token_usage"] is None
    assert evaluate_gate(summary, [result], {
        "min_recall_at_5": 0.9, "require_actual_cost": True
    })["status"] == "INSUFFICIENT_DATA"
    assert evaluate_gate(summary, [result], {
        "min_recall_at_5": 0.9
    })["status"] == "PASS"
    first = paired_comparison([result], [result])
    assert first == paired_comparison([result], [result])
    assert first["metrics"]["rule_task_success"]["delta"] == 0.0


def test_actual_token_aggregation_requires_complete_provider_usage():
    metrics = score_case(case(), artifact(prompt_tokens=12, completion_tokens=7))
    result = {"case_id": "c1", "status": "COMPLETED", "metrics": metrics,
              "duration_ms": 20, "artifact": {"tags": []}}
    assert aggregate_results([result], 1)["actual_token_usage"] == {
        "prompt_tokens": 12, "completion_tokens": 7,
    }
    missing = {**result, "case_id": "c2", "metrics": score_case(case(), artifact())}
    assert aggregate_results([result, missing], 2)["actual_token_usage"] is None


def test_security_case_with_invalid_citation_fails_gate_even_without_threshold():
    bad = score_case(case(), artifact(citations=[
        {"chunk_id": "a", "document_id": "d1", "page_number": 99,
         "quote": "期限为30天。"},
    ]))
    result = {"case_id": "security-1", "status": "COMPLETED", "metrics": bad,
              "duration_ms": 20, "artifact": {"tags": ["security"]}}
    gate = evaluate_gate(aggregate_results([result], 1), [result], {})
    assert gate["status"] == "FAIL"
    assert gate["safety_failures"] == ["security-1"]
