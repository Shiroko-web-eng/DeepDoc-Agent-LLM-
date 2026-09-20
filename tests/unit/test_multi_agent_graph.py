from app.agents.multi_graph import MultiAgentGraph


def test_multi_agent_router_keeps_simple_tasks_on_single_graph():
    choose = MultiAgentGraph.choose_mode
    assert choose("auto", "报销期限是多少", ["kb-1"], True) == (
        "single", "simple_fast_path"
    )
    assert choose("auto", "比较两份制度", ["kb-1", "kb-2"], True) == (
        "multi", "cross_knowledge_base_research"
    )
    assert choose("multi", "比较两份制度", ["kb-1"], False) == (
        "single", "multi_agent_disabled"
    )


def test_verifier_rejects_claim_without_matching_source_text():
    checked = MultiAgentGraph.check_claims({
        "evidence": [{"chunk_id": "chunk-1", "text": "期限为30天。",
                      "quote": "期限为30天。", "evidence_id": "ev_001"}],
        "claims": [
            {"claim_id": "c1", "chunk_id": "chunk-1", "text": "期限为30天。"},
            {"claim_id": "c2", "chunk_id": "chunk-1", "text": "期限为10天。"},
        ],
    })
    assert checked["verified_claims"][0]["verification"] == "supported"
    assert checked["verified_claims"][1]["verification"] == "unknown"
