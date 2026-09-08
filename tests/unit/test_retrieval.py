from app.retrieval import KeywordRetriever, tokenize


def test_tokenize_supports_chinese_and_latin_terms():
    tokens = tokenize("DeepDoc 支持文档问答")
    assert "deepdoc" in tokens
    assert "文档" in tokens


def test_keyword_retriever_ranks_matching_chunk_first():
    chunks = [
        {"id": "c1", "ordinal": 1, "text": "苹果是一种水果。"},
        {"id": "c2", "ordinal": 2, "text": "DeepDoc 支持文档引用和问答。"},
    ]
    result = KeywordRetriever().search("DeepDoc 如何支持文档问答？", chunks)
    assert result[0]["id"] == "c2"
    assert result[0]["score"] > 0
    assert [chunk["id"] for chunk in result] == ["c2"]


def test_keyword_retriever_respects_context_budget():
    chunks = [
        {"id": f"c{index}", "ordinal": index, "text": "目标 " + "x" * 100}
        for index in range(1, 5)
    ]
    result = KeywordRetriever().search("目标", chunks, max_chars=150)
    assert len(result) == 1
