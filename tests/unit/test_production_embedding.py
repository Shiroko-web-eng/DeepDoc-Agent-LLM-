from dataclasses import replace

import httpx
import pytest

from app.embedding import OpenAICompatibleEmbeddingProvider
from app.errors import AppError
from app.repository import Repository
from tests.integration.test_api import make_settings


def test_openai_compatible_embedding_orders_and_validates_vectors(tmp_path, monkeypatch):
    settings = replace(
        make_settings(tmp_path), embedding_provider="openai-compatible",
        embedding_model="embed", embedding_base_url="https://model.example/v1",
        embedding_dimensions=3,
    )
    provider = OpenAICompatibleEmbeddingProvider(settings)
    payloads = [
        {"data": [
            {"index": 1, "embedding": [0, 1, 0]},
            {"index": 0, "embedding": [1, 0, 0]},
        ]},
        {"data": [{"index": 0, "embedding": [1, 0]}]},
    ]

    def fake_post(url, **kwargs):
        assert url == "https://model.example/v1/embeddings"
        assert kwargs["json"]["dimensions"] == 3
        return httpx.Response(
            200, json=payloads.pop(0), request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("app.embedding.httpx.post", fake_post)
    assert provider.embed_documents(["first", "second"]) == [
        [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    ]
    with pytest.raises(AppError) as error:
        provider.embed_query("bad")
    assert error.value.code == "INVALID_EMBEDDING_RESPONSE"


class FakeCursor:
    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.sql = ""
        self.parameters = []

    def execute(self, sql, parameters):
        self.sql = sql
        self.parameters = parameters
        assert sql.count("?") == len(parameters)
        return FakeCursor()


class FakePostgresDatabase:
    is_postgres = True

    def __init__(self):
        self.connection = FakeConnection()

    def connect(self):
        connection = self.connection

        class Context:
            def __enter__(self):
                return connection

            def __exit__(self, *args):
                return False

        return Context()


def test_postgres_hybrid_candidate_query_binds_vector_fts_and_document_scope():
    database = FakePostgresDatabase()
    repository = Repository(database)
    assert repository.get_postgres_hybrid_candidates(
        "kb", [1.0, 0.0], "travel policy", 4, 5, ["doc-a", "doc-b"]
    ) == []
    assert "<=> ?::vector" in database.connection.sql
    assert "plainto_tsquery" in database.connection.sql
    assert database.connection.parameters == [
        "kb", "[1.0, 0.0]", "doc-a", "doc-b", 4,
        "kb", "travel policy", "doc-a", "doc-b", "travel policy", 5,
    ]
