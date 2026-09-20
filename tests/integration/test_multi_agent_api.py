import sqlite3

from fastapi.testclient import TestClient

from app.main import create_app
from app.generation import GeneratedAnswer
from tests.integration.test_agent_api import make_settings


def add_knowledge_base(client: TestClient, name: str, text: str) -> str:
    knowledge_base_id = client.post(
        "/v1/knowledge-bases", json={"name": name}
    ).json()["id"]
    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (f"{name}.txt", text.encode(), "text/plain")},
    )
    assert response.status_code == 202
    return knowledge_base_id


def test_multi_agent_routes_parallel_research_and_verifies_citations(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        travel_id = add_knowledge_base(
            client, "Travel", "差旅报销必须在费用发生后30天内提交。部门负责人审批。"
        )
        purchase_id = add_knowledge_base(
            client, "Purchase", "采购报销必须在验收后15天内提交。财务负责人审批。"
        )
        created = client.post("/v1/agent/runs", json={
            "question": "比较差旅报销和采购报销的期限与审批差异",
            "knowledge_base_ids": [travel_id, purchase_id],
            "execution_mode": "auto",
            "output_format": "comparison_report",
        })
        assert created.status_code == 202
        run_id = created.json()["id"]
        run = client.get(f"/v1/agent/runs/{run_id}").json()
        assert run["execution_mode"] == "multi"
        assert run["route_reason"] == "cross_knowledge_base_research"
        assert run["graph_version"] == "multi-v1"
        assert run["status"] == "COMPLETED"
        assert len(run["evidence"]) >= 2
        assert run["citations"]
        assert len(run["plan"]) == 2
        tasks = client.get(f"/v1/agent/runs/{run_id}/tasks").json()
        assert len(tasks) == 2
        assert all(task["status"] == "SUCCEEDED" for task in tasks)
        assert {task["knowledge_base_id"] for task in tasks} == {travel_id, purchase_id}
        assert client.get(f"/v1/agent/runs/{run_id}/tasks/t1").status_code == 200
        assert client.get(f"/v1/agent/runs/{run_id}/tasks/missing").status_code == 404
        events = client.get(f"/v1/agent/runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: task.delegated" in events.text
        assert events.text.count("event: task.delegated") == 1
        assert events.text.count("event: task.succeeded") == 2
        checkpoint_path = tmp_path / "agent-langgraph.sqlite"
        assert checkpoint_path.exists()
        with sqlite3.connect(checkpoint_path) as connection:
            checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (run_id,)
            ).fetchone()[0]
        assert checkpoint_count >= 4


def test_single_fast_path_and_explicit_multi_budget(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        kb = add_knowledge_base(client, "Rules", "报销期限是30天。")
        single = client.post("/v1/agent/runs", json={
            "question": "报销期限是多少？", "knowledge_base_ids": [kb],
        })
        run = client.get(f"/v1/agent/runs/{single.json()['id']}").json()
        assert run["execution_mode"] == "single"
        assert run["status"] == "COMPLETED"

        multi = client.post("/v1/agent/runs", json={
            "question": "分析报销期限", "knowledge_base_ids": [kb],
            "execution_mode": "multi",
            "budget": {"max_subtasks": 1, "max_parallel_agents": 1},
        })
        assert multi.status_code == 202
        run = client.get(f"/v1/agent/runs/{multi.json()['id']}").json()
        assert run["execution_mode"] == "multi"
        assert run["budget"]["max_subtasks"] == 1
        assert run["usage"]["subtasks"] == 1
        assert run["status"] == "COMPLETED"


def test_multi_agent_budget_cancel_and_resume(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        kb = add_knowledge_base(client, "Policy", "差旅报销期限为30天。")
        limited = client.post("/v1/agent/runs", json={
            "question": "分析差旅报销期限", "knowledge_base_ids": [kb],
            "execution_mode": "multi", "budget": {"max_tool_calls": 0},
        })
        limited_run = client.get(f"/v1/agent/runs/{limited.json()['id']}").json()
        assert limited_run["status"] == "BUDGET_EXCEEDED"
        assert client.get(f"/v1/agent/runs/{limited_run['id']}/tasks").json() == []

        service = app.state.agent_service
        repository = app.state.agent_repository
        queued = service.create_run(
            question="分析差旅报销期限", knowledge_base_ids=[kb],
            allow_web_search=False, output_format="research_brief",
            budget_overrides={}, execution_mode="multi",
        )
        repository.request_cancel(queued["id"])
        service.execute(queued["id"])
        assert repository.get_run(queued["id"])["status"] == "CANCELLED"
        repository.reset_for_resume(queued["id"])
        service.execute(queued["id"], resume=True)
        assert repository.get_run(queued["id"])["status"] == "COMPLETED"
        assert len(repository.list_tasks(queued["id"])) == 1


def test_multi_agent_rejects_invalid_scope_and_budget_schema(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        invalid_scope = client.post("/v1/agent/runs", json={
            "question": "分析制度", "knowledge_base_ids": ["missing"],
            "execution_mode": "multi",
        })
        assert invalid_scope.status_code == 404
        invalid_budget = client.post("/v1/agent/runs", json={
            "question": "分析制度", "knowledge_base_ids": ["missing"],
            "execution_mode": "multi", "budget": {"max_parallel_agents": 0},
        })
        assert invalid_budget.status_code == 422


def test_multi_agent_keeps_partial_evidence_when_one_research_task_fails(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        first = add_knowledge_base(client, "First", "采购审批期限为15天。")
        second = add_knowledge_base(client, "Second", "差旅审批期限为30天。")
        service = app.state.agent_service
        original = service.tools.kb.execute

        def fail_second(query, knowledge_base_ids, limit_per_base=6):
            if second in knowledge_base_ids:
                raise RuntimeError("injected retrieval failure")
            return original(query, knowledge_base_ids, limit_per_base)

        service.tools.kb.execute = fail_second
        created = client.post("/v1/agent/runs", json={
            "question": "比较采购和差旅审批期限", "knowledge_base_ids": [first, second],
            "execution_mode": "multi",
        })
        run_id = created.json()["id"]
        run = client.get(f"/v1/agent/runs/{run_id}").json()
        assert run["status"] == "PARTIAL"
        assert run["citations"]
        statuses = {item["status"] for item in client.get(
            f"/v1/agent/runs/{run_id}/tasks"
        ).json()}
        assert statuses == {"SUCCEEDED", "FAILED"}


def test_multi_agent_rejects_generated_invalid_citation(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        kb = add_knowledge_base(client, "Citation", "报销期限为30天。")

        class BadLLM:
            def generate(self, question, evidence):
                return GeneratedAnswer("期限为30天 [C1] [C999]", [1, 999], "test")

        app.state.agent_service.multi_graph.llm = BadLLM()
        created = client.post("/v1/agent/runs", json={
            "question": "分析报销期限", "knowledge_base_ids": [kb],
            "execution_mode": "multi",
        })
        run = client.get(f"/v1/agent/runs/{created.json()['id']}").json()
        assert run["status"] == "INSUFFICIENT"
        assert run["error_code"] == "CITATION_VALIDATION_FAILED"
        assert run["citations"] == []


def test_multi_agent_calculation_requires_numbers_in_document_evidence(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        kb = add_knowledge_base(client, "Numbers", "差旅期限为30天，采购期限为15天。")
        grounded = client.post("/v1/agent/runs", json={
            "question": "请根据文档计算 30-15", "knowledge_base_ids": [kb],
            "execution_mode": "multi",
        })
        grounded_run = client.get(f"/v1/agent/runs/{grounded.json()['id']}").json()
        assert grounded_run["status"] == "COMPLETED"
        assert "30-15 = 15" in grounded_run["answer"]
        assert len(client.get(f"/v1/agent/runs/{grounded_run['id']}/tasks").json()) == 2

        ungrounded = client.post("/v1/agent/runs", json={
            "question": "请根据文档计算 30-99", "knowledge_base_ids": [kb],
            "execution_mode": "multi",
        })
        ungrounded_run = client.get(f"/v1/agent/runs/{ungrounded.json()['id']}").json()
        assert ungrounded_run["status"] == "PARTIAL"
        assert ungrounded_run["error_code"] == "UNVERIFIED_CALCULATION"
        assert "30-99 =" not in ungrounded_run["answer"]
