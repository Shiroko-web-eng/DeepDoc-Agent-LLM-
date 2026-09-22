from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_documents(name):
    return list(yaml.safe_load_all(
        (ROOT / "deploy" / "kubernetes" / name).read_text(encoding="utf-8")
    ))


def test_production_compose_separates_migration_api_worker_and_stateful_services():
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "minio", "minio-init", "migrate", "api", "worker"} <= services.keys()
    assert services["migrate"]["command"] == ["python", "-m", "app.migrate"]
    assert services["worker"]["command"] == ["python", "-m", "app.worker"]
    assert services["api"]["environment"]["DEEPDOC_TASK_MODE"] == "durable"


def test_kubernetes_api_has_three_probes_security_budget_and_availability_controls():
    deployment, service, disruption, autoscaler = load_documents("api.yaml")
    assert [item["kind"] for item in (deployment, service, disruption, autoscaler)] == [
        "Deployment", "Service", "PodDisruptionBudget", "HorizontalPodAutoscaler"
    ]
    assert deployment["spec"]["replicas"] == 3
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert {"startupProbe", "readinessProbe", "livenessProbe"} <= container.keys()
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert disruption["spec"]["minAvailable"] == 2
    assert autoscaler["spec"]["minReplicas"] == 3


def test_kubernetes_workers_are_queue_scaled_and_eval_is_isolated():
    worker = load_documents("worker.yaml")
    evaluator = load_documents("eval-worker.yaml")
    assert [item["kind"] for item in worker] == [
        "Deployment", "ScaledObject", "Deployment", "ScaledObject"
    ]
    assert [item["kind"] for item in evaluator] == ["Deployment", "ScaledObject"]
    regular_kinds = worker[0]["spec"]["template"]["spec"]["containers"][0]["env"][0]
    eval_kinds = evaluator[0]["spec"]["template"]["spec"]["containers"][0]["env"][0]
    assert regular_kinds["value"] == "document.process,knowledge_base.reindex"
    agent_kinds = worker[2]["spec"]["template"]["spec"]["containers"][0]["env"][0]
    assert agent_kinds["value"] == "agent.execute"
    assert eval_kinds["value"] == "evaluation.execute"
    assert "evaluation.execute" in evaluator[1]["spec"]["triggers"][0]["metadata"]["query"]


def test_postgres_migration_enables_vector_rls_and_durable_jobs():
    migration = (ROOT / "app" / "migrations" / "001_production.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "embedding vector(256)" in migration
    assert "USING hnsw" in migration
    assert "USING gin" in migration
    assert "CREATE TABLE IF NOT EXISTS durable_jobs" in migration
    assert "CREATE TABLE IF NOT EXISTS request_idempotency" in migration
    assert "FUNCTION deepdoc_queued_jobs" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "current_setting('app.tenant_id'" in migration
    checkpoint_rls = (
        ROOT / "app" / "migrations" / "002_langgraph_rls.sql"
    ).read_text(encoding="utf-8")
    assert "thread_id LIKE current_setting" in checkpoint_rls
    assert "checkpoint_writes" in checkpoint_rls
