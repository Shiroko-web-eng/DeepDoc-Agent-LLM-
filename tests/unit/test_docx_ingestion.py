from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.errors import AppError
from app.ingestion import detect_media_type, parse_document


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


def test_docx_detection_and_parsing_preserve_paragraphs():
    content = make_docx(["第一段", "第二段包含结论"])

    assert detect_media_type("report.docx", content) == DOCX_TYPE
    assert parse_document(DOCX_TYPE, content) == ["第一段\n\n第二段包含结论"]


def test_invalid_docx_is_rejected():
    with pytest.raises(AppError) as error:
        detect_media_type("broken.docx", b"not-a-zip")
    assert error.value.code == "INVALID_FILE_CONTENT"
