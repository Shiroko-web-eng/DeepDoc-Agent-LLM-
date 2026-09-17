from app.embedding import HashingEmbeddingProvider, cosine_similarity
from app.retrieval import HybridRetriever, QueryRewriter


def with_embeddings(chunks, embedder):
    vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    return [{**chunk, "embedding": vector} for chunk, vector in zip(chunks, vectors, strict=True)]


def test_hashing_embeddings_are_deterministic_and_normalized():
    embedder = HashingEmbeddingProvider(64)
    first = embedder.embed_query("DeepDoc 文档检索")
    second = embedder.embed_query("DeepDoc 文档检索")
    unrelated = embedder.embed_query("天气预报")

    assert first == second
    assert round(cosine_similarity(first, first), 6) == 1.0
    assert cosine_similarity(first, unrelated) < 1.0


def test_query_rewriter_returns_bounded_semantic_and_lexical_queries():
    rewritten = QueryRewriter().rewrite("  DeepDoc   如何检索文档？ ")

    assert rewritten.semantic_query == "DeepDoc 如何检索文档？"
    assert rewritten.lexical_queries[0] == rewritten.semantic_query
    assert 1 <= len(rewritten.lexical_queries) <= 3


def test_hybrid_retriever_combines_bm25_dense_rrf_and_reranking():
    embedder = HashingEmbeddingProvider(64)
    chunks = with_embeddings([
        {
            "id": "c1", "document_id": "d1", "filename": "fruit.txt",
            "page_number": 1, "ordinal": 1, "text": "苹果是一种常见水果。",
        },
        {
            "id": "c2", "document_id": "d2", "filename": "rag.txt",
            "page_number": 1, "ordinal": 1,
            "text": "DeepDoc 使用 BM25 与向量检索实现混合搜索。",
        },
        {
            "id": "c3", "document_id": "d2", "filename": "rag.txt",
            "page_number": 2, "ordinal": 2, "text": "RRF 融合多个候选排名。",
        },
    ], embedder)
    retriever = HybridRetriever(embedder, rrf_k=60)

    rewritten, hits = retriever.search("DeepDoc 如何实现混合检索？", chunks, limit=3)

    assert rewritten.semantic_query == "DeepDoc 如何实现混合检索？"
    assert hits[0]["id"] == "c2"
    assert hits[0]["sparse_rank"] == 1
    assert hits[0]["rrf_score"] > 0
    assert hits[0]["rerank_score"] >= hits[-1]["rerank_score"]
    assert all("dense_rank" in hit and "sparse_score" in hit for hit in hits)


def test_hybrid_retriever_respects_limit_and_context_budget():
    embedder = HashingEmbeddingProvider(64)
    chunks = with_embeddings([
        {
            "id": f"c{index}", "document_id": f"d{index}",
            "filename": f"{index}.txt", "page_number": 1, "ordinal": index,
            "text": "目标文档 " + "内容" * 100,
        }
        for index in range(1, 4)
    ], embedder)

    _, hits = HybridRetriever(embedder).search(
        "目标文档", chunks, limit=3, max_chars=250
    )

    assert len(hits) == 1
