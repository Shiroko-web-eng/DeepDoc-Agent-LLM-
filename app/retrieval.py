from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Protocol


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_]{2,}", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    bigrams = ["".join(chinese[index:index + 2]) for index in range(len(chinese) - 1)]
    return latin + chinese + bigrams


@dataclass(frozen=True)
class RewrittenQuery:
    original: str
    semantic_query: str
    lexical_queries: list[str]


class QueryRewriter:
    """Bounded rule rewrite with the contract of a future LLM rewriter."""

    def rewrite(self, query: str) -> RewrittenQuery:
        normalized = re.sub(r"\s+", " ", query.strip())
        keywords = list(dict.fromkeys(tokenize(normalized)))
        lexical = [normalized]
        compact = " ".join(keywords[:12])
        if compact and compact != normalized.lower():
            lexical.append(compact)
        return RewrittenQuery(normalized, normalized, lexical[:3])


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class HybridRetriever:
    def __init__(self, embedder: Embedder, *, dense_top_k: int = 40,
                 sparse_top_k: int = 40, rerank_top_k: int = 12,
                 rrf_k: int = 60, max_per_document: int = 15):
        self.embedder = embedder
        self.dense_top_k = dense_top_k
        self.sparse_top_k = sparse_top_k
        self.rerank_top_k = rerank_top_k
        self.rrf_k = rrf_k
        self.max_per_document = max_per_document
        self.rewriter = QueryRewriter()

    def search(self, query: str, chunks: list[dict[str, Any]], limit: int = 8,
               max_chars: int = 16000) -> tuple[RewrittenQuery, list[dict[str, Any]]]:
        rewritten = self.rewriter.rewrite(query)
        if not chunks:
            return rewritten, []
        dense_scores = self._dense_scores(rewritten.semantic_query, chunks)
        sparse_scores = self._bm25_scores(rewritten.lexical_queries, chunks)
        dense_order = self._rank(dense_scores, chunks, self.dense_top_k)
        sparse_order = self._rank(sparse_scores, chunks, self.sparse_top_k)
        fused = self._fuse(dense_order, sparse_order)
        candidates = self._rerank(
            rewritten.original, chunks, dense_scores, sparse_scores, fused
        )
        selected: list[dict[str, Any]] = []
        document_counts: Counter[str] = Counter()
        used_chars = 0
        for candidate in candidates:
            document_id = candidate.get("document_id", "")
            if document_counts[document_id] >= self.max_per_document:
                continue
            if selected and used_chars + len(candidate["text"]) > max_chars:
                continue
            selected.append(candidate)
            document_counts[document_id] += 1
            used_chars += len(candidate["text"])
            if len(selected) >= min(limit, self.rerank_top_k):
                break
        return rewritten, selected

    def _dense_scores(self, query: str, chunks: list[dict[str, Any]]) -> dict[str, float]:
        query_vector = self.embedder.embed_query(query)
        scores: dict[str, float] = {}
        for chunk in chunks:
            vector = chunk.get("embedding") or []
            if len(vector) != len(query_vector):
                scores[chunk["id"]] = 0.0
                continue
            scores[chunk["id"]] = sum(
                left * right for left, right in zip(query_vector, vector, strict=True)
            )
        return scores

    def _bm25_scores(self, queries: list[str], chunks: list[dict[str, Any]]) -> dict[str, float]:
        tokenized = [tokenize(chunk["text"]) for chunk in chunks]
        lengths = [len(tokens) for tokens in tokenized]
        average_length = sum(lengths) / max(1, len(lengths))
        document_frequency: Counter[str] = Counter()
        for terms in tokenized:
            document_frequency.update(set(terms))
        scores = defaultdict(float)
        k1, b = 1.5, 0.75
        for query in queries:
            query_terms = Counter(tokenize(query))
            for chunk, terms, length in zip(chunks, tokenized, lengths, strict=True):
                frequencies = Counter(terms)
                score = 0.0
                for term, query_frequency in query_terms.items():
                    frequency = frequencies[term]
                    if not frequency:
                        continue
                    frequency_docs = document_frequency[term]
                    inverse_frequency = math.log(
                        1 + (len(chunks) - frequency_docs + 0.5) / (frequency_docs + 0.5)
                    )
                    denominator = frequency + k1 * (
                        1 - b + b * length / max(1.0, average_length)
                    )
                    score += query_frequency * inverse_frequency * (
                        frequency * (k1 + 1) / denominator
                    )
                scores[chunk["id"]] = max(scores[chunk["id"]], score)
        return dict(scores)

    @staticmethod
    def _rank(scores: dict[str, float], chunks: list[dict[str, Any]], limit: int) -> list[str]:
        ordinals = {chunk["id"]: chunk.get("ordinal", 0) for chunk in chunks}
        return [
            chunk_id for chunk_id, score in sorted(
                scores.items(), key=lambda item: (-item[1], ordinals[item[0]])
            )[:limit] if score > 0
        ]

    def _fuse(self, dense_order: list[str], sparse_order: list[str]) -> dict[str, dict[str, Any]]:
        fused: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"rrf_score": 0.0, "dense_rank": None, "sparse_rank": None}
        )
        for name, order in (("dense", dense_order), ("sparse", sparse_order)):
            for rank, chunk_id in enumerate(order, 1):
                fused[chunk_id][f"{name}_rank"] = rank
                fused[chunk_id]["rrf_score"] += 1.0 / (self.rrf_k + rank)
        return dict(fused)

    def _rerank(self, query: str, chunks: list[dict[str, Any]],
                dense_scores: dict[str, float], sparse_scores: dict[str, float],
                fused: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        query_terms = set(tokenize(query))
        candidates = []
        for chunk in chunks:
            chunk_id = chunk["id"]
            if chunk_id not in fused:
                continue
            chunk_terms = set(tokenize(chunk["text"]))
            coverage = len(query_terms & chunk_terms) / max(1, len(query_terms))
            rrf_score = fused[chunk_id]["rrf_score"]
            dense_score = dense_scores.get(chunk_id, 0.0)
            sparse_score = sparse_scores.get(chunk_id, 0.0)
            normalized_sparse = sparse_score / (1.0 + sparse_score)
            rerank_score = (
                0.45 * coverage
                + 0.30 * max(0.0, dense_score)
                + 0.25 * normalized_sparse
            )
            candidates.append({
                **chunk,
                **fused[chunk_id],
                "dense_score": dense_score,
                "sparse_score": sparse_score,
                "rerank_score": rerank_score,
                "score": rerank_score,
            })
        return sorted(
            candidates,
            key=lambda item: (
                -item["rerank_score"], -item["rrf_score"], item.get("ordinal", 0)
            ),
        )


class KeywordRetriever:
    """Compatibility adapter retained for the MVP tests and rollback path."""

    def search(self, query: str, chunks: list[dict[str, Any]], limit: int = 8,
               max_chars: int = 16000) -> list[dict[str, Any]]:
        query_terms = Counter(tokenize(query))
        if not query_terms:
            return chunks[:limit]
        document_frequency = Counter()
        tokenized = []
        for chunk in chunks:
            terms = Counter(tokenize(chunk["text"]))
            tokenized.append(terms)
            document_frequency.update(terms.keys())
        scored = []
        total = max(1, len(chunks))
        for chunk, terms in zip(chunks, tokenized, strict=True):
            score = sum(
                query_count * terms[term]
                * (math.log((total + 1) / (document_frequency[term] + 1)) + 1)
                for term, query_count in query_terms.items()
            )
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]["ordinal"]))
        selected, used = [], 0
        for score, chunk in scored:
            if score <= 0 and selected:
                continue
            if selected and used + len(chunk["text"]) > max_chars:
                continue
            selected.append({**chunk, "score": score})
            used += len(chunk["text"])
            if len(selected) >= limit:
                break
        return selected or chunks[:1]
