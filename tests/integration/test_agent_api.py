import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_path=tmp_path / "agent.db",
        upload_dir=tmp_path / "uploads",
        max_file_bytes=1024 * 1024,
        max_context_chars=8000,
        llm_provider="extractive",
        llm_model="",
        llm_base_url="",
        llm_api_key="",
        llm_timeout_seconds=1,
        log_level="WARNING",
        embedding_dimensions=64,
        agent_max_duration_seconds=30,
        agent_max_nodes=20,
        agent_max_retrieval_rounds=2,
        agent_max_tool_calls=4,
    )


def setup_knowledge_base(client: TestClient) -> tuple[str, list[str]]:
    knowledge_base_id = client.post(
        "/v1/knowledge-bases", json={"name": "Agent 测试知识库"}
    ).json()["id"]
    documents = []
    for filename, text in (
        ("travel.txt", "差旅报销必须在费用发生后30天内提交，并由部门负责人审批。"),
        ("purchase.txt", "采购报销必须在验收后15天内提交，并由财务负责人审批。"),
    ):
        response = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, text.encode(), "text/plain")},
        )
        assert response.status_code == 202
        documents.append(response.json()["id"])
    return knowledge_base_id, documents


def parse_sse(body: str) -> list[tuple[int, str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event_id = int(next(line[4:] for line in lines if line.startswith("id: ")))
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        events.append((event_id, name, data))
    return events


def test_agent_comparison_run_persists_plan_evidence_checkpoints_and_events(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        knowledge_base_id, _ = setup_knowledge_base(client)

        created = client.post(
            "/v1/agent/runs",
            json={
                "question": "比较差旅报销和采购报销的期限与审批差异",
                "knowledge_base_ids": [knowledge_base_id],
                "output_format": "comparison_report",
            },
        )
        assert created.status_code == 202
        assert created.json()["status"] == "QUEUED"
        run_id = created.json()["id"]

        run = client.get(f"/v1/agent/runs/{run_id}")
        assert run.status_code == 200
        payload = run.json()
        assert payload["status"] == "COMPLETED"
        assert payload["task_type"] == "COMPARISON"
        assert len(payload["plan"]) == 2
        assert len(payload["evidence"]) >= 2
        assert payload["citations"]
        assert "[C" in payload["answer"]
        assert payload["usage"]["nodes"] <= payload["budget"]["max_nodes"]

        assert client.get(f"/v1/agent/runs/{run_id}/steps").json() == payload["plan"]
        assert client.get(f"/v1/agent/runs/{run_id}/evidence").json() == payload["evidence"]

        events_response = client.get(f"/v1/agent/runs/{run_id}/events")
        events = parse_sse(events_response.text)
        names = [name for _, name, _ in events]
        assert names[0] == "run.created"
        assert "node.plan" in names
        assert "evidence.added" in names
        assert names[-1] == "run.completed"

        resumed_events = client.get(
            f"/v1/agent/runs/{run_id}/events",
            headers={"Last-Event-ID": str(events[-2][0])},
        )
        assert len(parse_sse(resumed_events.text)) == 1

        with app.state.repository.database.connect() as connection:
            checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM agent_checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        assert checkpoint_count >= 8


def test_agent_calculator_tool_and_tool_registry(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        knowledge_base_id, _ = setup_knowledge_base(client)

        created = client.post(
            "/v1/agent/runs",
            json={
                "question": "文档中的期限分别是30天和15天，请计算 30-15",
                "knowledge_base_ids": [knowledge_base_id],
            },
        )
        run = client.get(f"/v1/agent/runs/{created.json()['id']}").json()

        assert run["status"] == "COMPLETED"
        assert run["task_type"] == "CALCULATION"
        assert "30-15 = 15" in run["answer"]
        assert run["usage"]["tool_calls"] == 1

        tools = client.get("/v1/agent/tools")
        assert tools.status_code == 200
        by_name = {tool["name"]: tool for tool in tools.json()}
        assert by_name["knowledge_base.search"]["available"] is True
        assert by_name["calculator.evaluate"]["available"] is True
        assert by_name["web.search"]["available"] is False


def test_agent_budget_stops_graph_and_invalid_scope_is_rejected(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        knowledge_base_id, _ = setup_knowledge_base(client)
        limited = client.post(
            "/v1/agent/runs",
            json={
                "question": "总结两份制度",
                "knowledge_base_ids": [knowledge_base_id],
                "budget": {"max_nodes": 4},
            },
        )
        run = client.get(f"/v1/agent/runs/{limited.json()['id']}").json()
        assert run["status"] == "BUDGET_EXCEEDED"
        assert run["answer"] == "任务已达到运行预算上限。"

        missing = client.post(
            "/v1/agent/runs",
            json={"question": "研究内容", "knowledge_base_ids": ["missing"]},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_agent_cancel_before_execution_and_resume_from_checkpoint(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        knowledge_base_id, _ = setup_knowledge_base(client)
        service = app.state.agent_service
        repository = app.state.agent_repository
        run = service.create_run(
            question="总结报销制度",
            knowledge_base_ids=[knowledge_base_id],
            allow_web_search=False,
            output_format="structured_summary",
            budget_overrides={},
        )
        repository.request_cancel(run["id"])

        service.execute(run["id"])
        cancelled = repository.get_run(run["id"])
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["answer"] == "任务已取消。"

        repository.reset_for_resume(run["id"])
        service.execute(run["id"], resume=True)
        resumed = repository.get_run(run["id"])
        assert resumed["status"] == "COMPLETED"
        assert resumed["citations"]
        event_names = [
            event["event_type"] for event in repository.list_events(run["id"])
        ]
        assert "run.cancelled" in event_names
        assert "run.resumed" in event_names
        assert event_names[-1] == "run.completed"
