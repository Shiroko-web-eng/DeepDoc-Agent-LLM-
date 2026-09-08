from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_]{2,}", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    bigrams = ["".join(chinese[index:index + 2]) for index in range(len(chinese) - 1)]
    return latin + chinese + bigrams


class KeywordRetriever:
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
                query_count * terms[term] * (math.log((total + 1) / (document_frequency[term] + 1)) + 1)
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

