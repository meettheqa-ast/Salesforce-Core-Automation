"""CSV / Excel file parser for the test-case import wizard.

Goals:
  - Accept ``.csv``, ``.xlsx``, ``.xls`` uploads sent to
    ``POST /api/imports/test-cases/parse``.
  - Decode CSV files robustly (UTF-8 BOM, Windows-1252, Latin-1) without
    a chardet dependency -- enterprise QA spreadsheets exported from
    Excel routinely arrive as cp1252.
  - Read XLSX via pandas + openpyxl (both already in requirements.txt).
    Prefer a sheet named anything from {tests, test cases, test_cases,
    scenarios} when multiple sheets exist; fall back to sheet 0.
  - Cap at IMPORT_MAX_ROWS (default 10,000) and IMPORT_MAX_FILE_BYTES
    (default 32 MB). Above either cap the parser raises so the router
    can return a clean 413.
  - Preserve original header strings (the mapping UI shows them
    verbatim to the user); only lowercase for matching.

Return shape (the dataclass ``ParsedFile``):
  - ``columns``: list[str]  -- original headers, in source order
  - ``rows``: list[dict[str, str]] -- already string-normalised values
  - ``sheet_name``: str | None -- which XLSX sheet was read
  - ``encoding``: str | None -- which decoder succeeded for CSV

Out of scope here: column mapping (``import_mapping.py``) + duplicate
detection (``import_dedupe.py``). This module only does I/O + parsing.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_qa_portal.import_parser")

# Sentinels for callers. Both are configurable per-deploy via env vars
# so an operator can tighten them without a code change.
IMPORT_MAX_ROWS = int(os.environ.get("IMPORT_MAX_ROWS", "10000"))
IMPORT_MAX_FILE_BYTES = int(os.environ.get("IMPORT_MAX_FILE_BYTES", str(32 * 1024 * 1024)))
IMPORT_MAX_CELL_BYTES = int(os.environ.get("IMPORT_MAX_CELL_BYTES", "65536"))

# Encodings tried in order for CSV. utf-8-sig handles the BOM Excel
# loves to add; cp1252 covers most Windows exports; latin-1 is the
# everything-decodes-to-something safety net.
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# When an XLSX has multiple sheets, prefer one of these. Match is
# case-insensitive + whitespace-collapsed.
_PREFERRED_SHEET_NAMES = (
    "tests",
    "test cases",
    "test_cases",
    "testcases",
    "scenarios",
    "cases",
)


class ImportParseError(Exception):
    """Raised when the file is unreadable / malformed. The router
    translates this to a 400 with the message in the detail."""


class ImportTooLargeError(Exception):
    """Raised when the file exceeds IMPORT_MAX_FILE_BYTES or
    IMPORT_MAX_ROWS. The router translates this to a 413 with a clear
    message telling the user to split or trim the file."""


@dataclass
class ParsedFile:
    """Result of reading a single upload."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    sheet_name: str | None = None
    encoding: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)


def detect_kind(filename: str) -> str:
    """Map a filename's extension to a ``source_kind`` value the
    ImportBatch model accepts. Defaults to ``csv`` for unknown
    extensions (we'll try to parse as CSV anyway)."""
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"xlsx"}:
        return "xlsx"
    if suffix in {"xls"}:
        return "xls"
    return "csv"


def parse_bytes(filename: str, blob: bytes) -> ParsedFile:
    """Top-level entry: dispatch on extension. Caller passes the
    original filename so we can pick csv vs xlsx without needing the
    request to specify."""
    if not blob:
        raise ImportParseError("Empty upload.")
    if len(blob) > IMPORT_MAX_FILE_BYTES:
        raise ImportTooLargeError(
            f"File is {len(blob):,} bytes; the import limit is "
            f"{IMPORT_MAX_FILE_BYTES:,} bytes. Split the file and try again.",
        )
    kind = detect_kind(filename)
    if kind in ("xlsx", "xls"):
        return _parse_excel(blob, kind=kind)
    return _parse_csv(blob)


def _parse_csv(blob: bytes) -> ParsedFile:
    """Decode + parse a CSV blob. Tries a small encoding chain rather
    than pulling in chardet -- the chain covers the ~99% of QA
    exports we see in practice (Excel-saved CSV, Google-saved CSV,
    and ad-hoc text editors)."""
    text, encoding = _decode_csv(blob)

    # Sniff the dialect (delimiter) on the first ~8KB. csv.Sniffer
    # raises Error when it can't decide; fall back to a permissive
    # default that handles both `,` and `;` separators commonly used
    # outside the US.
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.get_dialect("excel")  # comma + minimal quoting

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    raw_columns = list(reader.fieldnames or [])
    columns = [c.strip() for c in raw_columns if c and c.strip()]
    if not columns:
        raise ImportParseError(
            "Could not detect any column headers. The first row of the "
            "CSV must contain the column names (Title, Steps, Expected "
            "Result, ...).",
        )

    rows: list[dict[str, str]] = []
    for raw in reader:
        if len(rows) >= IMPORT_MAX_ROWS:
            raise ImportTooLargeError(
                f"File exceeds the {IMPORT_MAX_ROWS:,}-row limit. Split "
                "the spreadsheet and re-upload.",
            )
        row: dict[str, str] = {}
        for col in columns:
            value = raw.get(col, "") or ""
            if not isinstance(value, str):
                value = str(value)
            if len(value.encode("utf-8", errors="ignore")) > IMPORT_MAX_CELL_BYTES:
                # Surface as an inline error rather than aborting the
                # whole import -- pathological cells (a 1MB embedded
                # screenshot) should not block the rest of the file.
                value = value[: IMPORT_MAX_CELL_BYTES // 4] + "... (truncated)"
            row[col] = value
        # Skip completely empty rows (every cell blank). Easy CSV
        # editing artifact that would otherwise turn into a "row N
        # missing title" validation error during commit.
        if any(v.strip() for v in row.values()):
            rows.append(row)
    return ParsedFile(columns=columns, rows=rows, encoding=encoding)


def _decode_csv(blob: bytes) -> tuple[str, str]:
    """Try each encoding in turn; return the first one that decodes
    without error. The fallback is latin-1 which never raises -- worst
    case a few mojibake characters slip through, which the user can
    fix in the mapping preview rather than us silently dropping the
    whole file."""
    for enc in _CSV_ENCODINGS:
        try:
            return blob.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # latin-1 was in the list, so we should never get here -- but in
    # case the env override list is shorter, fall back unconditionally.
    return blob.decode("latin-1", errors="replace"), "latin-1"


def _parse_excel(blob: bytes, *, kind: str) -> ParsedFile:
    """Read an XLSX (or legacy XLS) workbook via pandas. The streaming
    ``nrows=`` cap protects us from OOM on a 100k-row sheet someone
    forgot to trim.

    The first sheet whose name matches `_PREFERRED_SHEET_NAMES` wins;
    otherwise we read sheet index 0. We do NOT silently merge multi-
    sheet workbooks -- the user can re-upload each tab separately if
    they want all of them imported.
    """
    try:
        import pandas as pd  # noqa: PLC0415 -- heavy dep, lazy-imported
    except ImportError as exc:
        raise ImportParseError(
            "Excel parsing requires the pandas package (already in "
            "ai_qa_portal/requirements.txt). Install it and retry.",
        ) from exc

    bio = io.BytesIO(blob)
    engine = "openpyxl" if kind == "xlsx" else None  # let pandas pick xlrd for .xls

    try:
        # First read just the sheet names so we can pick the right one
        # without loading every sheet's data into memory.
        with pd.ExcelFile(bio, engine=engine) as xls:
            chosen_sheet = _pick_sheet(xls.sheet_names)
            # ``nrows`` is honoured by both engines and keeps the read
            # streaming; without it a 200k-row sheet would still load.
            df = xls.parse(
                sheet_name=chosen_sheet,
                nrows=IMPORT_MAX_ROWS + 1,
                dtype=str,
                keep_default_na=False,
            )
    except Exception as exc:  # noqa: BLE001
        raise ImportParseError(
            f"Could not read Excel file: {exc}. Re-save as .xlsx and try again.",
        ) from exc

    if len(df) > IMPORT_MAX_ROWS:
        raise ImportTooLargeError(
            f"Sheet '{chosen_sheet}' has {len(df):,} rows; the import limit is "
            f"{IMPORT_MAX_ROWS:,}. Split the file and re-upload.",
        )

    columns = [str(c).strip() for c in df.columns if str(c).strip()]
    rows: list[dict[str, str]] = []
    for _, raw in df.iterrows():
        row: dict[str, str] = {}
        for col in columns:
            value = raw.get(col, "")
            value = "" if value is None else str(value)
            if len(value.encode("utf-8", errors="ignore")) > IMPORT_MAX_CELL_BYTES:
                value = value[: IMPORT_MAX_CELL_BYTES // 4] + "... (truncated)"
            row[col] = value
        if any(v.strip() for v in row.values()):
            rows.append(row)
    return ParsedFile(columns=columns, rows=rows, sheet_name=chosen_sheet)


def _pick_sheet(sheet_names: list[Any]) -> Any:
    """Return the most-likely test-case sheet from a workbook. Falls
    back to sheet[0] (which is what the prior context_parser.py did
    too). The exact-match scan is case-insensitive and tolerates
    arbitrary internal whitespace ('Test Cases' == 'test_cases')."""
    if not sheet_names:
        return 0

    def _norm(s: Any) -> str:
        return "".join(str(s).lower().split()).replace("_", "")

    preferred = {_norm(p) for p in _PREFERRED_SHEET_NAMES}
    for s in sheet_names:
        if _norm(s) in preferred:
            return s
    return sheet_names[0]
