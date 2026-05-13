"""Tests for the context-file parser.

Covers CSV row parsing, XLSX row parsing (skipped when openpyxl/pandas
aren't installed), markdown / text chunking, and the unsupported-extension
guard. These are pure unit tests -- no DB, no HTTP.
"""

from __future__ import annotations

import io

import pytest

from ai_qa_portal.backend.services.context_parser import (
    infer_kind,
    parse_bytes,
)


def test_infer_kind_known_extensions():
    assert infer_kind("data.csv") == "csv"
    assert infer_kind("notes.MD") == "md"
    assert infer_kind("brief.PDF") == "pdf"


def test_infer_kind_rejects_unknown():
    with pytest.raises(ValueError):
        infer_kind("script.exe")


def test_parse_csv_keeps_columns_and_searchable_text():
    blob = b"role,description\nSales Manager,Owns leads\nService Agent,Handles cases\n"
    result = parse_bytes(filename="user_types.csv", blob=blob)
    assert result.kind == "csv"
    assert result.columns == ["role", "description"]
    assert len(result.rows) == 2
    first = result.rows[0]
    assert first.data == {"role": "Sales Manager", "description": "Owns leads"}
    assert "role: Sales Manager" in first.searchable_text
    assert "description: Owns leads" in first.searchable_text


def test_parse_csv_handles_bom_and_empty_cells():
    blob = "\ufeffname,note\nAlice,\nBob,hi\n".encode("utf-8")
    result = parse_bytes(filename="x.csv", blob=blob)
    # BOM should be stripped so the first column is `name`, not `\ufeffname`.
    assert result.columns[0] == "name"
    assert result.rows[0].data == {"name": "Alice", "note": ""}


def test_parse_markdown_chunks_text():
    body = ("This is a sentence. " * 600).encode("utf-8")
    result = parse_bytes(filename="doc.md", blob=body)
    assert result.kind == "md"
    assert result.chunks, "expected at least one chunk for non-empty markdown"
    for chunk in result.chunks:
        assert chunk.text.strip(), "chunk text must not be whitespace-only"
        assert chunk.token_count > 0


def test_parse_txt_short_returns_single_chunk():
    result = parse_bytes(filename="hello.txt", blob=b"Hello world.")
    assert result.kind == "txt"
    assert len(result.chunks) == 1
    assert "Hello world." in result.chunks[0].text


def test_parse_xlsx_when_pandas_available():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    df = pd.DataFrame([{"role": "Admin", "level": "1"}, {"role": "Agent", "level": "2"}])
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    blob = buf.getvalue()
    result = parse_bytes(filename="data.xlsx", blob=blob)
    assert result.kind == "xlsx"
    assert result.columns == ["role", "level"]
    assert len(result.rows) == 2
