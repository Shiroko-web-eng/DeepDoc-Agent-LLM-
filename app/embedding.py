from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.errors import AppError

from app.retrieval import tokenize


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbeddingProvider:
    """Deterministic local embedding for development and repeatable tests."""

    model_name = "hashing-embedding-v1"

    def __init__(self, dimensions: int = 256):
        if dimensions < 32:
            raise ValueError("embedding dimensions must be at least 32")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model_name = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self.base_url = settings.embedding_base_url or settings.llm_base_url
        self.api_key = settings.embedding_api_key or settings.llm_api_key

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._request(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]

    def _request(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.post(
                f"{self.base_url.rstrip('/')}/embeddings", headers=headers,
                json={"model": self.model_name, "input": texts,
                      "dimensions": self._dimensions},
                timeout=self.settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in ordered]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AppError("EMBEDDING_CALL_FAILED", "Embedding 调用失败", 502, True) from exc
        if len(vectors) != len(texts) or any(
            not isinstance(vector, list) or len(vector) != self._dimensions
            or any(not isinstance(value, (int, float)) for value in vector)
            for vector in vectors
        ):
            raise AppError("INVALID_EMBEDDING_RESPONSE", "Embedding 返回维度无效", 502)
        return [[float(value) for value in vector] for vector in vectors]


def build_embedding(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai-compatible":
        return OpenAICompatibleEmbeddingProvider(settings)
    return HashingEmbeddingProvider(settings.embedding_dimensions)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right, strict=True))
