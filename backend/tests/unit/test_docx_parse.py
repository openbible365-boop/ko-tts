import io

import pytest
from docx import Document

from app.docx_parse import DocxParseError, parse_docx_lines


def _make_docx(paragraphs: list[str]) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_parses_nonempty_paragraphs_in_order():
    data = _make_docx(["첫째 줄입니다", "둘째 줄입니다", "셋째 줄입니다"])
    assert parse_docx_lines(data) == ["첫째 줄입니다", "둘째 줄입니다", "셋째 줄입니다"]


def test_skips_blank_lines_and_trims_whitespace():
    data = _make_docx(["  앞뒤 공백 있음  ", "", "   ", "\t", "다음 줄"])
    assert parse_docx_lines(data) == ["앞뒤 공백 있음", "다음 줄"]


def test_empty_document_returns_empty_list():
    assert parse_docx_lines(_make_docx([])) == []


def test_invalid_file_raises_docx_parse_error():
    with pytest.raises(DocxParseError):
        parse_docx_lines(b"this is definitely not a docx file")
