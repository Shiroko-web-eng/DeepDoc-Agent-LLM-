from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from pypdf import PdfReader

from app.errors import AppError


SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def detect_media_type(filename: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    media_type = SUPPORTED_TYPES.get(extension)
    if not media_type:
        raise AppError("UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、TXT 和 Markdown 文件", 415)
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise AppError("INVALID_FILE_CONTENT", "文件内容不是有效 PDF", 400)
    if extension == ".docx" and not content.startswith(b"PK"):
        raise AppError("INVALID_FILE_CONTENT", "文件内容不是有效 DOCX", 400)
    if extension not in {".pdf", ".docx"}:
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
    elif media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        pages = [_parse_docx(content)]
    else:
        pages = [content.decode("utf-8")]
    normalized = [normalize_text(page) for page in pages]
    if not any(normalized):
        raise AppError("EMPTY_DOCUMENT", "文档没有可提取文本", 422)
    return normalized


def _parse_docx(content: bytes) -> str:
    try:
        with ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise AppError("DOCX_PARSE_FAILED", "DOCX 解析失败", 422) from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n\n".join(paragraphs)


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
