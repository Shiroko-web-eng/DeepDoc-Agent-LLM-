import pytest

from app.errors import AppError
from app.ingestion import chunk_pages, detect_media_type, normalize_text, parse_document


def test_detects_supported_text_and_rejects_spoofed_pdf():
    assert detect_media_type("notes.txt", "内容".encode()) == "text/plain"
    with pytest.raises(AppError) as error:
        detect_media_type("fake.pdf", b"not a pdf")
    assert error.value.code == "INVALID_FILE_CONTENT"


def test_parse_normalizes_text_and_rejects_empty_document():
    assert parse_document("text/plain", b"first\r\n\r\n\r\nsecond") == ["first\n\nsecond"]
    with pytest.raises(AppError) as error:
        parse_document("text/plain", b" \n ")
    assert error.value.code == "EMPTY_DOCUMENT"


def test_chunk_pages_keeps_page_and_offsets():
    pages = [normalize_text("第一段。\n\n" + "长内容" * 900), "第二页结论。"]
    chunks = chunk_pages("doc", pages, target=200, maximum=300, overlap=20)
    assert len(chunks) > 2
    assert chunks[0]["id"] == "doc:p1:c1"
    assert chunks[-1]["page_number"] == 2
    assert all(chunk["end_offset"] > chunk["start_offset"] for chunk in chunks)
