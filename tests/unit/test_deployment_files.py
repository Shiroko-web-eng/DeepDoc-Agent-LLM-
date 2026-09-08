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

