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
        max_context_chars=4000,
        llm_provider="extractive",
        llm_model="",
        llm_base_url="",
        llm_api_key="",
        llm_timeout_seconds=1,
        log_level="WARNING",
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


def test_document_upload_question_run_and_delete(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        upload = client.post(
            "/v1/documents",
            files={"file": ("guide.txt", "DeepDoc 是文档问答系统。\n\n它为答案提供原文引用。".encode(), "text/plain")},
        )
        assert upload.status_code == 202
        document_id = upload.json()["id"]

        document = client.get(f"/v1/documents/{document_id}")
        assert document.status_code == 200
        assert document.json()["status"] == "READY"

        response = client.post(
            f"/v1/documents/{document_id}/questions",
            json={"question": "DeepDoc 是什么？"},
        )
        assert response.status_code == 200
        events = parse_sse(response.text)
        names = [name for name, _ in events]
        assert names[0] == "metadata"
        assert "answer_delta" in names
        assert "citation" in names
        assert names[-1] == "done"

        run_id = events[0][1]["run_id"]
        run = client.get(f"/v1/qa-runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "COMPLETED"
        assert run.json()["citations"][0]["page_number"] == 1

        deleted = client.delete(f"/v1/documents/{document_id}")
        assert deleted.status_code == 202
        assert client.get(f"/v1/documents/{document_id}").status_code == 404


def test_duplicate_upload_is_idempotent(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        files = {"file": ("same.md", b"# Same\n\nContent", "text/markdown")}
        first = client.post("/v1/documents", files=files)
        second = client.post(
            "/v1/documents",
            files={"file": ("renamed.md", b"# Same\n\nContent", "text/markdown")},
        )
        assert first.json()["id"] == second.json()["id"]
        assert len(client.get("/v1/documents").json()) == 1


def test_invalid_file_and_missing_answer_are_handled(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        invalid = client.post(
            "/v1/documents",
            files={"file": ("bad.exe", b"data", "application/octet-stream")},
        )
        assert invalid.status_code == 415
        assert invalid.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
        assert invalid.headers["X-Request-ID"]

        upload = client.post(
            "/v1/documents",
            files={"file": ("guide.txt", "只介绍苹果。".encode(), "text/plain")},
        )
        document_id = upload.json()["id"]
        answer = client.post(
            f"/v1/documents/{document_id}/questions",
            json={"question": "量子计算的结论是什么？"},
        )
        events = parse_sse(answer.text)
        text = "".join(data["text"] for name, data in events if name == "answer_delta")
        assert text == "当前文档无法支持该结论。"
        assert all(name != "citation" for name, _ in events)


def test_health_and_validation_errors(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/docs"
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        response = client.post("/v1/documents/missing/questions", json={"question": ""})
        assert response.status_code == 422
        blank = client.post("/v1/documents/missing/questions", json={"question": "   "})
        assert blank.status_code == 422
