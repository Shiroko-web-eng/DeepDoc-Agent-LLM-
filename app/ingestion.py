from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.errors import AppError


SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def detect_media_type(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    media_type = SUPPORTED_TYPES.get(extension)
    if not media_type:
        raise AppError("UNSUPPORTED_FILE_TYPE", "仅支持 PDF、TXT 和 Markdown 文件", 415)
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise AppError("INVALID_FILE_CONTENT", "文件内容不是有效 PDF", 400)
    if extension != ".pdf":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_TEXT_ENCODING", "文本文件必须使用 UTF-8 编码", 400) from exc
    return media_type


def parse_document(media_type: str, content: bytes) -> list[str]:
    if media_type == "application/pdf":
        try:
            pages = [(page.extract_text() or "") for page in PdfReader(BytesIO(content)).pages]
        except Exception as exc:
            raise AppError("PDF_PARSE_FAILED", "PDF 解析失败", 422) from exc
    else:
        pages = [content.decode("utf-8")]
    normalized = [normalize_text(page) for page in pages]
    if not any(normalized):
        raise AppError("EMPTY_DOCUMENT", "文档没有可提取文本", 422)
    return normalized


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_pages(doc_id: str, pages: list[str], target: int = 1000, maximum: int = 2000,
                overlap: int = 100) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    ordinal = 0
    for page_number, page in enumerate(pages, 1):
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", page) if item.strip()]
        buffer = ""
        start = 0
        page_chunks: list[tuple[str, int, int]] = []
        for paragraph in paragraphs:
            if buffer and len(buffer) + len(paragraph) + 2 > target:
                page_chunks.extend(_split_large(buffer, start, maximum, overlap))
                start += len(buffer) + 2
                buffer = paragraph
            else:
                buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if buffer:
            page_chunks.extend(_split_large(buffer, start, maximum, overlap))
        for text, begin, end in page_chunks:
            ordinal += 1
            chunks.append({
                "id": f"{doc_id}:p{page_number}:c{ordinal}",
                "document_id": doc_id,
                "page_number": page_number,
                "ordinal": ordinal,
                "text": text,
                "start_offset": begin,
                "end_offset": end,
            })
    return chunks


def _split_large(text: str, start: int, maximum: int, overlap: int) -> list[tuple[str, int, int]]:
    if len(text) <= maximum:
        return [(text, start, start + len(text))]
    result = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + maximum, len(text))
        if end < len(text):
            boundary = max(text.rfind("。", cursor, end), text.rfind(". ", cursor, end),
                           text.rfind("\n", cursor, end))
            if boundary > cursor + maximum // 2:
                end = boundary + 1
        piece = text[cursor:end].strip()
        if piece:
            result.append((piece, start + cursor, start + end))
        if end >= len(text):
            break
        cursor = max(cursor + 1, end - overlap)
    return result

