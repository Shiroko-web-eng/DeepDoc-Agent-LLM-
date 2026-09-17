import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_path=tmp_path / "deepdoc.db",
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
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


def upload(client: TestClient, knowledge_base_id: str, filename: str, text: str) -> str:
    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, text.encode(), "text/plain")},
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_knowledge_base_hybrid_search_question_citations_and_reindex(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/v1/knowledge-bases",
            json={"name": "财务制度", "description": "报销规则"},
        )
        assert created.status_code == 201
        knowledge_base_id = created.json()["id"]
        travel_id = upload(
            client, knowledge_base_id, "travel.txt",
            "差旅报销必须在费用发生后30天内提交。",
        )
        purchase_id = upload(
            client, knowledge_base_id, "purchase.txt",
            "采购报销需要在验收后15天内提交。",
        )

        detail = client.get(f"/v1/knowledge-bases/{knowledge_base_id}").json()
        assert detail["document_count"] == 2
        assert detail["active_index_version"] >= 3

        search = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/search",
            json={"question": "采购报销需要多久提交？", "limit": 5},
        )
        assert search.status_code == 200
        payload = search.json()
        assert payload["hits"][0]["document_id"] == purchase_id
        assert payload["hits"][0]["sparse_rank"] == 1
        assert payload["hits"][0]["rrf_score"] > 0

        answer = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/questions",
            json={
                "question": "采购报销期限是什么？",
                "document_ids": [travel_id, purchase_id],
            },
        )
        assert answer.status_code == 200
        events = parse_sse(answer.text)
        citations = [data for name, data in events if name == "citation"]
        assert citations
        assert citations[0]["document_id"] == purchase_id
        assert citations[0]["filename"] == "purchase.txt"
        assert events[-1][0] == "done"

        run_id = events[0][1]["run_id"]
        run = client.get(f"/v1/qa-runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["citations"][0]["filename"] == "purchase.txt"
        assert run.json()["knowledge_base_id"] == knowledge_base_id
        assert run.json()["retrieval_trace"]["hits"][0]["chunk_id"]

        reindex = client.post(f"/v1/knowledge-bases/{knowledge_base_id}/reindex")
        assert reindex.status_code == 202
        job = client.get(f"/v1/index-jobs/{reindex.json()['id']}")
        assert job.json()["status"] == "ACTIVE"
        assert job.json()["index_version"] > detail["active_index_version"]


def test_document_filter_cannot_cross_knowledge_base_boundary(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        first = client.post("/v1/knowledge-bases", json={"name": "A"}).json()["id"]
        second = client.post("/v1/knowledge-bases", json={"name": "B"}).json()["id"]
        upload(client, first, "public.txt", "公开制度允许查询。")
        secret_id = upload(client, second, "secret.txt", "机密预算是一百万元。")

        response = client.post(
            f"/v1/knowledge-bases/{first}/search",
            json={"question": "机密预算", "document_ids": [secret_id]},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "DOCUMENT_NOT_IN_KNOWLEDGE_BASE"


def test_question_rejects_empty_knowledge_base_before_streaming(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        knowledge_base_id = client.post(
            "/v1/knowledge-bases", json={"name": "空知识库"}
        ).json()["id"]

        response = client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/questions",
            json={"question": "有什么内容？"},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_READY"
