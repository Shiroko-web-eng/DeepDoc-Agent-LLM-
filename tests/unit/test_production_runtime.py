from dataclasses import replace

import pytest

from app.config import Settings
from app.db import Database
from app.jobs import DurableJobQueue, Worker
from app.idempotency import IdempotencyStore
from app.errors import AppError
from app.storage import LocalObjectStorage
from tests.integration.test_api import make_settings


def production_settings(tmp_path) -> Settings:
    return replace(
        make_settings(tmp_path), environment="production",
        database_url="postgresql://deepdoc:secret@db/deepdoc",
        task_mode="durable", auto_migrate=False, storage_backend="s3",
        s3_bucket="documents", auth_mode="oidc",
        oidc_issuer="https://id.example", oidc_audience="deepdoc",
        oidc_jwks_url="https://id.example/.well-known/jwks.json",
        llm_provider="openai-compatible", llm_model="model",
        llm_base_url="https://model.example/v1", otel_endpoint="http://otel:4318",
        embedding_provider="openai-compatible", embedding_model="embedding-model",
        embedding_base_url="https://model.example/v1",
    )


def test_production_configuration_fails_closed_and_complete_config_passes(tmp_path):
    with pytest.raises(ValueError, match="production configuration missing"):
        replace(make_settings(tmp_path), environment="production").validate()
    production_settings(tmp_path).validate()
    with pytest.raises(ValueError, match="AUTO_MIGRATE"):
        replace(production_settings(tmp_path), auto_migrate=True).validate()
    with pytest.raises(ValueError, match="TASK_MODE"):
        replace(make_settings(tmp_path), task_mode="unknown").validate()


def test_local_object_storage_rejects_escape_and_round_trips(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    locator = storage.locator("documents/file.txt")
    storage.write(locator, b"evidence")
    assert storage.read(locator) == b"evidence"
    storage.delete(locator)
    assert not (tmp_path / "objects" / "documents" / "file.txt").exists()
    with pytest.raises(ValueError, match="escapes"):
        storage.locator("../secret")


class RecordingDispatcher:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def dispatch(self, kind, payload):
        self.calls.append((kind, payload))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("injected")


def test_durable_job_worker_filters_kinds_retries_and_completes(tmp_path):
    settings = replace(make_settings(tmp_path), task_mode="durable",
                       worker_max_attempts=2, worker_lease_seconds=5)
    database = Database(settings.database_path)
    database.initialize()
    queue = DurableJobQueue(database, settings)
    skipped = queue.enqueue("evaluation.execute", {"run_id": "eval"})
    selected = queue.enqueue("agent.execute", {"run_id": "agent"})
    dispatcher = RecordingDispatcher(failures=1)
    worker = Worker(queue, dispatcher, worker_id="worker-1",
                    allowed_kinds=("agent.execute",))

    assert worker.run_once() is True
    first = queue.get(selected["id"])
    assert first["status"] == "QUEUED"
    assert first["attempts"] == 1
    assert queue.get(skipped["id"])["status"] == "QUEUED"

    with database.connect() as connection:
        connection.execute(
            "UPDATE durable_jobs SET available_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", selected["id"]),
        )
    assert worker.run_once() is True
    assert queue.get(selected["id"])["status"] == "COMPLETED"
    assert queue.get(selected["id"])["attempts"] == 2
    assert dispatcher.calls == [
        ("agent.execute", {"run_id": "agent"}),
        ("agent.execute", {"run_id": "agent"}),
    ]


def test_durable_job_reaches_dead_letter_after_max_attempts(tmp_path):
    settings = replace(make_settings(tmp_path), task_mode="durable",
                       worker_max_attempts=1, worker_lease_seconds=5)
    database = Database(settings.database_path)
    database.initialize()
    queue = DurableJobQueue(database, settings)
    job = queue.enqueue("agent.execute", {"run_id": "bad"})
    worker = Worker(queue, RecordingDispatcher(failures=1), worker_id="worker-2")
    assert worker.run_once() is True
    assert queue.get(job["id"])["status"] == "DEAD"
    assert queue.get(job["id"])["error_code"] == "RuntimeError"


def test_production_requires_idempotency_key_for_guarded_creation(tmp_path):
    settings = replace(make_settings(tmp_path), environment="production")
    database = Database(settings.database_path)
    database.initialize()
    store = IdempotencyStore(database, settings)
    with pytest.raises(AppError) as error:
        store.run(route="agent-runs.create", key=None, request={}, factory=lambda: {})
    assert error.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    assert error.value.status_code == 428
