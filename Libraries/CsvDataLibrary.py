"""Robot Framework library: load CSV rows as dicts for data-driven FOR loops."""

from __future__ import annotations

import csv
import re
from pathlib import Path


# Lowercase header (after trim) -> canonical key used by generated tests
_HEADER_ALIASES: dict[str, str] = {
    "zip": "PostalCode",
    "zip code": "PostalCode",
    "postal code": "PostalCode",
    "zip/postal code": "PostalCode",
    "state/province": "State",
    "lead source": "LeadSource",
    "lead status": "Status",
}


def _normalize_header(header: str) -> str:
    h = (header or "").strip()
    if not h:
        return h
    lk = h.lower().replace("-", " ").replace("/", " ")
    while "  " in lk:
        lk = lk.replace("  ", " ")
    if lk in _HEADER_ALIASES:
        return _HEADER_ALIASES[lk]
    parts = re.split(r"[\s_]+", lk)
    parts = [p for p in parts if p]
    return "".join(p.capitalize() for p in parts) if parts else h


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in row.items():
        nk = _normalize_header(k)
        if nk and nk not in out:
            out[nk] = v or ""
    return out


class CsvDataLibrary:
    """Load UTF-8-SIG CSV files into Python dicts (Robot accesses ``${row}[Key]``)."""

    def load_csv_as_list_of_dicts(self, path: str) -> list[dict[str, str]]:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"CSV not found: {p.resolve()}")
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return [_normalize_row(dict(r)) for r in reader]
