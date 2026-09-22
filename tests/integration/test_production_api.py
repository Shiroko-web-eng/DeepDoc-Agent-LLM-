from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.tenant import Principal
from tests.integration.test_api import make_settings


def test_durable_mode_persists_work_before_worker_executes(tmp_path):
    settings = replace(make_settings(tmp_path), task_mode="durable")
    app = create_app(settings)
    with TestClient(app) as client:
        uploaded = client.post(
            "/v1/documents",
            files={"file": ("policy.txt", "报销期限为30天。".encode(), "text/plain")},
        )
        assert uploaded.status_code == 202
        document_id = uploaded.json()["id"]
        assert client.get(f"/v1/documents/{document_id}").json()["status"] == "PENDING"
        assert app.state.job_queue.stats() == {"QUEUED": 1}

        assert app.state.worker.run_once() is True
        assert client.get(f"/v1/documents/{document_id}").json()["status"] == "READY"
        assert app.state.job_queue.stats() == {"COMPLETED": 1}


def test_creation_idempotency_replays_response_and_rejects_body_change(tmp_path):
    app = create_app(make_settings(tmp_path))
    headers = {"Idempotency-Key": "kb-create-0001"}
    with TestClient(app) as client:
        first = client.post("/v1/knowledge-bases", json={"name": "Policy"},
                            headers=headers)
        repeated = client.post("/v1/knowledge-bases", json={"name": "Policy"},
                               headers=headers)
        assert repeated.status_code == 201
        assert repeated.json() == first.json()
        assert len(client.get("/v1/knowledge-bases").json()) == 2  # includes default
        conflict = client.post("/v1/knowledge-bases", json={"name": "Changed"},
                               headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_oidc_mode_protects_business_routes_but_not_health(tmp_path, monkeypatch):
    settings = replace(
        make_settings(tmp_path), auth_mode="oidc", oidc_issuer="https://id.example",
        oidc_audience="deepdoc", oidc_jwks_url="https://id.example/jwks",
    )
    def authenticate(self, header):
        if header == "Bearer valid":
            return Principal("user-1", "tenant-a", ("reader",))
        if header == "Bearer admin":
            return Principal("admin-1", "tenant-a", ("eval-admin",))
        return (_ for _ in ()).throw(
            __import__("app.errors", fromlist=["AppError"]).AppError(
                "INVALID_ACCESS_TOKEN", "访问令牌无效", 401
            )
        )

    monkeypatch.setattr("app.security.OIDCAuthenticator.authenticate", authenticate)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health/startup").status_code == 200
        denied = client.get("/v1/knowledge-bases")
        assert denied.status_code == 401
        allowed = client.get(
            "/v1/knowledge-bases", headers={"Authorization": "Bearer valid"}
        )
        assert allowed.status_code == 200
        assert allowed.headers["X-Request-ID"]
        reader_eval = client.get(
            "/v1/evaluations/datasets", headers={"Authorization": "Bearer valid"}
        )
        assert reader_eval.status_code == 403
        admin_eval = client.get(
            "/v1/evaluations/datasets", headers={"Authorization": "Bearer admin"}
        )
        assert admin_eval.status_code == 200
