"""Parse uploaded context artefacts into structured rows + text chunks.

Supported kinds (matched by extension):

* ``csv``   -- pandas read_csv, one row per CSV record
* ``xlsx``  -- openpyxl via pandas, one row per first-sheet record
* ``pdf``   -- pypdf, page-by-page text concatenation then chunked
* ``docx``  -- python-docx, paragraph-by-paragraph
* ``md``    -- raw text
* ``txt``   -- raw text

Free-text outputs are token-chunked with a sliding window so the
embedding step can index them directly. CSV/XLSX rows are kept whole
so prompts can address them by column ("use the row where Role = Sales
Manager").

The parser writes nothing to the database; it returns plain Python
objects so the router can wrap the writes in one transaction.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_qa_portal.context_parser")

# Chunking knobs tuned for OpenAI text-embedding-3-small (8k input cap).
# ~800 tokens leaves margin for prompt wrappers; 100-token overlap keeps
# sentence boundaries from getting clipped between chunks.
_CHUNK_TARGET_TOKENS = 800
_CHUNK_OVERLAP_TOKENS = 100
_FALLBACK_CHARS_PER_TOKEN = 4  # used when tiktoken is unavailable

KIND_BY_EXT = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
}


@dataclass(slots=True)
class ParsedRow:
    row_index: int
    data: dict[str, Any]
    searchable_text: str


@dataclass(slots=True)
class ParsedChunk:
    chunk_index: int
    text: str
    token_count: int


@dataclass(slots=True)
class ParseResult:
    kind: str
    sha256: str
    rows: list[ParsedRow] = field(default_factory=list)
    chunks: list[ParsedChunk] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


# ---- public entrypoint ----------------------------------------------------

def infer_kind(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in KIND_BY_EXT:
        raise ValueError(
            f"Unsupported file extension {ext!r}; supported: {sorted(KIND_BY_EXT)}"
        )
    return KIND_BY_EXT[ext]


def parse_bytes(*, filename: str, blob: bytes) -> ParseResult:
    """Parse ``blob`` and return rows + chunks. Filename drives the parser
    selection via extension; raises ``ValueError`` for unsupported types."""
    kind = infer_kind(filename)
    sha = hashlib.sha256(blob).hexdigest()

    if kind == "csv":
        rows, columns = _parse_csv(blob)
        return ParseResult(kind=kind, sha256=sha, rows=rows, columns=columns)
    if kind == "xlsx":
        rows, columns = _parse_xlsx(blob)
        return ParseResult(kind=kind, sha256=sha, rows=rows, columns=columns)
    if kind == "pdf":
        text = _parse_pdf(blob)
    elif kind == "docx":
        text = _parse_docx(blob)
    else:  # md / txt
        text = blob.decode("utf-8", errors="replace")

    chunks = chunk_text(text)
    return ParseResult(kind=kind, sha256=sha, chunks=chunks)


# ---- structured parsers ---------------------------------------------------

def _parse_csv(blob: bytes) -> tuple[list[ParsedRow], list[str]]:
    text = blob.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[ParsedRow] = []
    columns = list(reader.fieldnames or [])
    for idx, row in enumerate(reader):
        cleaned = {k: ("" if v is None else str(v).strip()) for k, v in row.items() if k}
        rows.append(ParsedRow(row_index=idx, data=cleaned, searchable_text=_row_to_text(cleaned)))
    return rows, columns


def _parse_xlsx(blob: bytes) -> tuple[list[ParsedRow], list[str]]:
    """Excel parsing via pandas; reads only the first sheet to keep the
    contract simple. Multi-sheet workbooks are explicitly out of scope for
    v1 -- they'd surface as separate context files instead."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover -- requirements pin pandas
        raise RuntimeError("pandas is required for .xlsx parsing") from exc
    df = pd.read_excel(io.BytesIO(blob), sheet_name=0, dtype=str).fillna("")
    columns = [str(c) for c in df.columns]
    rows: list[ParsedRow] = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        cleaned = {str(k): str(v).strip() for k, v in record.items()}
        rows.append(ParsedRow(row_index=idx, data=cleaned, searchable_text=_row_to_text(cleaned)))
    return rows, columns


def _row_to_text(row: dict[str, str]) -> str:
    """Flatten a structured row into a single ``key: value | key: value``
    line so it can be embedded / matched lexically."""
    return " | ".join(f"{k}: {v}" for k, v in row.items() if v)


# ---- free-text parsers ----------------------------------------------------

def _parse_pdf(blob: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover -- pinned in requirements
        raise RuntimeError("pypdf is required for .pdf parsing") from exc
    reader = PdfReader(io.BytesIO(blob))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 -- per-page failure isn't fatal
            logger.warning("PDF page extract failed: %s", exc)
            text = ""
        parts.append(text)
    return "\n\n".join(parts).strip()


def _parse_docx(blob: bytes) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover -- pinned in requirements
        raise RuntimeError("python-docx is required for .docx parsing") from exc
    document = docx.Document(io.BytesIO(blob))
    return "\n".join(p.text for p in document.paragraphs if p.text).strip()


# ---- chunking -------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def chunk_text(text: str) -> list[ParsedChunk]:
    """Split ``text`` into overlapping token-sized windows. Returns at
    least one chunk for non-empty inputs (or an empty list for empty
    inputs)."""
    text = text.strip()
    if not text:
        return []

    encoder = _try_load_tiktoken()
    if encoder is None:
        return _char_chunks(text)

    tokens = encoder.encode(text)
    if len(tokens) <= _CHUNK_TARGET_TOKENS:
        return [ParsedChunk(chunk_index=0, text=text, token_count=len(tokens))]

    out: list[ParsedChunk] = []
    step = _CHUNK_TARGET_TOKENS - _CHUNK_OVERLAP_TOKENS
    i = 0
    chunk_index = 0
    while i < len(tokens):
        window = tokens[i : i + _CHUNK_TARGET_TOKENS]
        decoded = encoder.decode(window).strip()
        if decoded:
            out.append(ParsedChunk(chunk_index=chunk_index, text=decoded, token_count=len(window)))
            chunk_index += 1
        i += step
    return out


def _try_load_tiktoken():
    """Return a tiktoken encoder if available; else ``None``. We import
    lazily so dev environments without the wheel still parse text (just
    fall back to character-based chunking)."""
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 -- absence is non-fatal
        return None


def _char_chunks(text: str) -> list[ParsedChunk]:
    target_chars = _CHUNK_TARGET_TOKENS * _FALLBACK_CHARS_PER_TOKEN
    overlap_chars = _CHUNK_OVERLAP_TOKENS * _FALLBACK_CHARS_PER_TOKEN
    step = target_chars - overlap_chars
    out: list[ParsedChunk] = []
    i = 0
    chunk_index = 0
    while i < len(text):
        window = text[i : i + target_chars].strip()
        if window:
            out.append(
                ParsedChunk(
                    chunk_index=chunk_index,
                    text=window,
                    token_count=len(window) // _FALLBACK_CHARS_PER_TOKEN,
                )
            )
            chunk_index += 1
        i += step
    return out
