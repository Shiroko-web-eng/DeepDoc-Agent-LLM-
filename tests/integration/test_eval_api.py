from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.test_agent_api import make_settings


EVAL_HEADERS = {"X-Eval-Token": "local-eval-secret"}


def make_eval_app(tmp_path):
    return create_app(replace(make_settings(tmp_path), eval_admin_token="local-eval-secret"))


def add_kb(client: TestClient, name: str, text: str) -> str:
    kb_id = client.post("/v1/knowledge-bases", json={"name": name}).json()["id"]
    uploaded = client.post(
        f"/v1/knowledge-bases/{kb_id}/documents",
        files={"file": (f"{name}.txt", text.encode(), "text/plain")},
    )
    assert uploaded.status_code == 202
    return kb_id


def dataset_body(app, kb_one: str, kb_two: str) -> dict:
    first = app.state.repository.get_knowledge_base_chunks(kb_one)[0]["id"]
    second = app.state.repository.get_knowledge_base_chunks(kb_two)[0]["id"]
    return {
        "name": "Expense benchmark", "version": "1.0.0", "split": "regression",
        "cases": [
            {
                "case_id": "travel", "task_type": "fact",
                "question": "差旅报销期限是什么？", "knowledge_base_ids": [kb_one],
                "gold_evidence_sets": [[first]],
                "expected_citation_chunk_ids": [first],
                "required_claims": ["30天"], "tags": ["fact"],
            },
            {
                "case_id": "compare", "task_type": "comparison",
                "question": "比较差旅和采购报销的期限差异",
                "knowledge_base_ids": [kb_one, kb_two],
                "gold_evidence_sets": [[first, second]],
                "expected_citation_chunk_ids": [first, second],
                "required_claims": ["30天", "15天"],
                "expected_mode": "multi", "tags": ["multi-kb"],
            },
        ],
    }


def test_eval_dataset_runs_metrics_gate_and_pairwise_comparison(tmp_path):
    app = make_eval_app(tmp_path)
    with TestClient(app, headers=EVAL_HEADERS) as client:
        first = add_kb(client, "Travel", "差旅报销期限为30天。")
        second = add_kb(client, "Purchase", "采购报销期限为15天。")
        body = dataset_body(app, first, second)
        created = client.post("/v1/evaluations/datasets", json=body)
        assert created.status_code == 201
        dataset = created.json()
        assert len(dataset["cases"]) == 2
        assert len(dataset["content_sha256"]) == 64
        assert client.get(f"/v1/evaluations/datasets/{dataset['id']}").status_code == 200
        assert len(client.get("/v1/evaluations/datasets").json()) == 1

        rag = client.post("/v1/evaluations/runs", json={
            "dataset_id": dataset["id"], "mode": "rag",
            "gates": {"min_recall_at_5": 0.0},
        })
        assert rag.status_code == 202
        rag_run = client.get(f"/v1/evaluations/runs/{rag.json()['id']}").json()
        assert rag_run["status"] == "COMPLETED"
        assert rag_run["summary"]["scored_cases"] == 1
        assert rag_run["summary"]["not_applicable"] == 1
        assert rag_run["summary"]["metrics"]["recall_at_5"]["count"] == 1
        assert rag_run["summary"]["actual_cost_usd"] is None
        assert rag_run["gate"]["status"] == "PASS"
        rag_cases = client.get(f"/v1/evaluations/runs/{rag_run['id']}/cases").json()
        assert {item["status"] for item in rag_cases} == {"COMPLETED", "NOT_APPLICABLE"}

        multi = client.post("/v1/evaluations/runs", json={
            "dataset_id": dataset["id"], "mode": "multi",
            "gates": {"min_citation_validity": 0.95, "require_actual_cost": True},
        })
        assert multi.status_code == 202
        multi_run = client.get(f"/v1/evaluations/runs/{multi.json()['id']}").json()
        assert multi_run["status"] == "COMPLETED"
        assert multi_run["summary"]["scored_cases"] == 2
        assert multi_run["gate"]["status"] == "INSUFFICIENT_DATA"
        assert multi_run["config"]["dataset_sha256"] == dataset["content_sha256"]
        comparison = client.get(
            f"/v1/evaluations/runs/{multi_run['id']}/comparison",
            params={"baseline": rag_run["id"]},
        )
        assert comparison.status_code == 200
        assert comparison.json()["common_cases"] == 2
        assert comparison.json()["metrics"]["recall_at_5"]["paired_count"] == 1


def test_eval_dataset_rejects_bad_gold_duplicate_version_and_changed_corpus(tmp_path):
    app = make_eval_app(tmp_path)
    with TestClient(app, headers=EVAL_HEADERS) as client:
        first = add_kb(client, "Travel", "差旅报销期限为30天。")
        second = add_kb(client, "Purchase", "采购报销期限为15天。")
        body = dataset_body(app, first, second)
        wrong = {**body, "version": "invalid"}
        wrong["cases"] = [{**body["cases"][0], "gold_evidence_sets": [["missing"]]}]
        assert client.post("/v1/evaluations/datasets", json=wrong).status_code == 400
        dataset = client.post("/v1/evaluations/datasets", json=body).json()
        duplicate = client.post("/v1/evaluations/datasets", json=body)
        assert duplicate.status_code == 409
        empty_gold = {**body, "version": "invalid-schema"}
        empty_gold["cases"] = [{**body["cases"][0], "gold_evidence_sets": []}]
        assert client.post("/v1/evaluations/datasets", json=empty_gold).status_code == 422

        reindexed = client.post(f"/v1/knowledge-bases/{first}/reindex")
        assert reindexed.status_code == 202
        run = client.post("/v1/evaluations/runs", json={
            "dataset_id": dataset["id"], "mode": "rag",
        })
        failed = client.get(f"/v1/evaluations/runs/{run.json()['id']}").json()
        assert failed["status"] == "FAILED"
        assert failed["summary"]["error_code"] == "CORPUS_CHANGED"
        assert failed["gate"]["status"] == "INSUFFICIENT_DATA"


def test_eval_cancel_before_execution_and_dataset_comparison_guard(tmp_path):
    app = make_eval_app(tmp_path)
    with TestClient(app, headers=EVAL_HEADERS) as client:
        first = add_kb(client, "Travel", "差旅报销期限为30天。")
        second = add_kb(client, "Purchase", "采购报销期限为15天。")
        dataset = client.post(
            "/v1/evaluations/datasets", json=dataset_body(app, first, second)
        ).json()
        service = app.state.eval_service
        repository = app.state.eval_repository
        from app.evaluation.models import EvalRunCreate

        queued = service.create_run(EvalRunCreate(dataset_id=dataset["id"], mode="single"))
        repository.request_cancel(queued["id"])
        service.execute(queued["id"])
        cancelled = repository.get_run(queued["id"])
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["summary"]["missing_cases"] == 2
        assert cancelled["gate"]["status"] == "INSUFFICIENT_DATA"
        service.execute(queued["id"])
        assert repository.get_run(queued["id"])["status"] == "CANCELLED"

        other_body = {**dataset_body(app, first, second), "version": "2.0.0"}
        other = client.post("/v1/evaluations/datasets", json=other_body).json()
        first_run = client.post("/v1/evaluations/runs", json={
            "dataset_id": dataset["id"], "mode": "single",
        }).json()
        other_run = client.post("/v1/evaluations/runs", json={
            "dataset_id": other["id"], "mode": "single",
        }).json()
        mismatch = client.get(
            f"/v1/evaluations/runs/{first_run['id']}/comparison",
            params={"baseline": other_run["id"]},
        )
        assert mismatch.status_code == 409


def test_eval_api_requires_configured_admin_token(tmp_path):
    app = make_eval_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/v1/evaluations/datasets").status_code == 403
        assert client.get("/v1/evaluations/datasets", headers={
            "X-Eval-Token": "wrong",
        }).status_code == 403
        assert client.get("/v1/evaluations/datasets", headers=EVAL_HEADERS).status_code == 200

    disabled = create_app(make_settings(tmp_path / "disabled"))
    with TestClient(disabled) as client:
        response = client.get("/v1/evaluations/datasets", headers=EVAL_HEADERS)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EVAL_NOT_CONFIGURED"


def test_eval_cli_returns_success_only_when_gate_passes(tmp_path, capsys):
    from app.evaluation.cli import main

    settings = replace(make_settings(tmp_path), eval_admin_token="local-eval-secret")
    app = create_app(settings)
    with TestClient(app, headers=EVAL_HEADERS) as client:
        kb_id = add_kb(client, "Travel", "差旅报销期限为30天。")
        chunk_id = app.state.repository.get_knowledge_base_chunks(kb_id)[0]["id"]
        body = {"name": "cli", "version": "1", "split": "regression", "cases": [{
            "case_id": "travel", "task_type": "fact", "question": "差旅报销期限是什么？",
            "knowledge_base_ids": [kb_id], "gold_evidence_sets": [[chunk_id]],
            "required_claims": ["30天"],
        }]}
        dataset = client.post("/v1/evaluations/datasets", json=body).json()

    assert main(["--dataset-id", dataset["id"], "--mode", "rag",
                 "--min-recall-at-5", "0"], settings) == 0
    assert '"status": "PASS"' in capsys.readouterr().out
    assert main(["--dataset-id", dataset["id"], "--mode", "rag"], settings) == 1
    assert '"status": "NOT_CONFIGURED"' in capsys.readouterr().out
