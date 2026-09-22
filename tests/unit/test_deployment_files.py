from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_file_defines_api_and_persistent_volume():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    api = compose["services"]["api"]
    assert api["build"] == "."
    assert "8000:8000" in api["ports"]
    assert "deepdoc-data:/app/data" in api["volumes"]
    assert "deepdoc-data" in compose["volumes"]


def test_dockerfile_runs_as_non_root_with_health_ready_application():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
    assert "uvicorn" in dockerfile
    assert "app.main:app" in dockerfile


def test_example_environment_contains_hybrid_rag_controls():
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEEPDOC_EMBEDDING_DIMENSIONS=256" in environment
    assert "DEEPDOC_DENSE_TOP_K=40" in environment
    assert "DEEPDOC_SPARSE_TOP_K=40" in environment
    assert "DEEPDOC_RERANK_TOP_K=12" in environment
    assert "DEEPDOC_RRF_K=60" in environment
    assert "DEEPDOC_AGENT_MAX_DURATION_SECONDS=45" in environment
    assert "DEEPDOC_AGENT_MAX_NODES=20" in environment
    assert "DEEPDOC_AGENT_MAX_RETRIEVAL_ROUNDS=3" in environment
    assert "DEEPDOC_MULTI_AGENT_ENABLED=true" in environment
    assert "DEEPDOC_MULTI_AGENT_MAX_SUBTASKS=4" in environment


def test_project_declares_supported_langgraph_dependency():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.0.0"' in project
    assert '"langgraph>=1.2.11,<2"' in project
    assert '"langgraph-checkpoint-sqlite>=3.1.1,<4"' in project
    assert '"psycopg[binary]>=3.2,<4"' in project
    assert '"opentelemetry-sdk>=1.38,<2"' in project
