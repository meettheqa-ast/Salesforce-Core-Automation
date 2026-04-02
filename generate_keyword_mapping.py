#!/usr/bin/env python3
"""
Scan Robot Framework keyword libraries under Resources/PO and Resources/Common,
emit keyword_catalog.json for RAG / AI knowledge bases.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCAN_DIRS = ("Resources/PO", "Resources/Common")
OUTPUT_FILE = "keyword_catalog.json"

SECTION_KEYWORDS = re.compile(r"^\*\*\*\s*Keywords\s*\*\*\*\s*$", re.IGNORECASE | re.MULTILINE)
NEXT_SECTION = re.compile(r"^\*\*\*\s*(?!Keywords)[^*]+\s*\*\*\*\s*$", re.IGNORECASE | re.MULTILINE)
DOC_START = re.compile(r"^\[Documentation\]\s+(.*)$", re.IGNORECASE)
ARGS_START = re.compile(r"^\[Arguments\]\s+(.*)$", re.IGNORECASE)
TAGS_LINE = re.compile(r"^\[Tags\]\s+", re.IGNORECASE)


def extract_keywords_body(text: str) -> str | None:
    m = SECTION_KEYWORDS.search(text)
    if not m:
        return None
    tail = text[m.end() :]
    m2 = NEXT_SECTION.search(tail)
    if m2:
        return tail[: m2.start()]
    return tail


def split_arguments_segment(segment: str) -> list[str]:
    """Split Robot [Arguments] payload on 2+ spaces (Robot cell separator)."""
    segment = segment.strip()
    if not segment:
        return []
    return [p.strip() for p in re.split(r" {2,}|\t+", segment) if p.strip()]


def collect_documentation(lines: list[str], start: int) -> tuple[str, int]:
    """
    Parse [Documentation] and following `...` continuation lines.
    Returns (doc_text, index_after_last_consumed_line).
    """
    line = lines[start]
    m = DOC_START.match(line.strip())
    if not m:
        return "", start
    parts = [m.group(1).strip()]
    j = start + 1
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            j += 1
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent == 0:
            break
        stripped = raw.strip()
        if stripped.startswith("..."):
            cont = stripped[3:].strip()
            if cont:
                parts.append(cont)
            j += 1
            continue
        if stripped.startswith("[") and not stripped.upper().startswith("[DOCUMENTATION]"):
            break
        break
    return " ".join(parts), j - 1


def collect_arguments(lines: list[str], start: int) -> tuple[list[str], int]:
    line = lines[start]
    m = ARGS_START.match(line.strip())
    if not m:
        return [], start
    buf = [m.group(1).strip()]
    j = start + 1
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            j += 1
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent == 0:
            break
        stripped = raw.strip()
        if stripped.startswith("..."):
            buf.append(stripped[3:].strip())
            j += 1
            continue
        break
    merged = "    ".join(buf)
    return split_arguments_segment(merged), j - 1


def suggest_natural_language_description(keyword_name: str) -> str:
    """
    Heuristic 'natural language' blurb when [Documentation] is missing.
    """
    name = keyword_name.strip()
    lower = name.lower()

    if lower.startswith("verify "):
        return f"Verifies that {name[7:]}."
    if lower.startswith("delete "):
        return f"Deletes or removes {name[7:]} in the Salesforce UI."
    if lower.startswith("open "):
        return f"Opens {name[5:]}."
    if lower.startswith("close "):
        return f"Closes {name[6:]}."
    if lower.startswith("enter "):
        return f"Enters data into {name[6:]}."
    if lower.startswith("select "):
        return f"Selects {name[7:]}."
    if lower.startswith("get "):
        return f"Retrieves or reads {name[4:]}."
    if lower.startswith("launch "):
        return f"Launches {name[7:]}."
    if lower.startswith("login "):
        return f"Performs login: {name}."
    if lower.startswith("create a new "):
        return f"Creates a new {name[13:]} using the current modal or form and saves when complete."
    if lower.startswith("create "):
        return f"Creates {name[7:]}."
    if lower.startswith("convert "):
        return f"Converts or transforms {name[8:]}."
    if lower.startswith("begin "):
        return f"Starts or initializes {name[6:]}."
    if lower.startswith("end "):
        return f"Ends or tears down {name[4:]}."
    if lower.startswith("perform "):
        return f"Performs the action: {name}."
    if lower.startswith("visit "):
        return f"Navigates to {name[6:]}."
    if lower.startswith("wait "):
        return f"Waits for {name[5:]}."
    if lower.startswith("click "):
        return f"Clicks {name[6:]}."
    if lower.startswith("return "):
        return f"Returns or navigates back regarding {name[7:]}."
    return (
        f"Robot Framework keyword that automates the workflow: {name}. "
        "Inspect the source file for step-level behavior."
    )


def parse_keyword_file(path: Path) -> list[dict]:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    body = extract_keywords_body(text)
    if not body or not body.strip():
        return []

    lines = body.splitlines()
    entries: list[dict] = []
    i = 0
    current_name: str | None = None
    doc = ""
    args: list[str] = []
    tags: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        leading = len(line) - len(line.lstrip(" \t"))
        if leading == 0 and not stripped.startswith("***"):
            if current_name is not None:
                entries.append(_finalize_entry(current_name, doc, args, tags, rel))
            current_name = stripped
            doc = ""
            args: list[str] = []
            tags: list[str] = []
            i += 1
            continue

        if current_name is None:
            i += 1
            continue

        us = stripped
        if DOC_START.match(us):
            doc, end_i = collect_documentation(lines, i)
            i = end_i + 1
            continue
        if ARGS_START.match(us):
            args, end_i = collect_arguments(lines, i)
            i = end_i + 1
            continue
        if TAGS_LINE.match(us):
            tag_part = TAGS_LINE.sub("", us, count=1).strip()
            tags = re.split(r"\s{2,}|\t+|\s+", tag_part) if tag_part else []
            tags = [t for t in tags if t]
            i += 1
            continue

        i += 1

    if current_name is not None:
        entries.append(_finalize_entry(current_name, doc, args, tags, rel))

    return entries


def _finalize_entry(
    name: str,
    doc: str,
    args: list[str],
    tags: list[str],
    rel: str,
) -> dict:
    has_doc = bool(doc and doc.strip())
    inferred = suggest_natural_language_description(name) if not has_doc else None
    return {
        "keyword_name": name,
        "arguments": args,
        "documentation": doc.strip() if has_doc else None,
        "documentation_source": "author" if has_doc else "inferred_only",
        "natural_language_summary": doc.strip() if has_doc else inferred,
        "inferred_description": inferred if not has_doc else None,
        "tags": tags,
        "source_file": rel,
    }


def main() -> None:
    files: list[Path] = []
    for sub in SCAN_DIRS:
        base = ROOT / sub
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob("*.robot")))

    keywords: list[dict] = []
    for f in files:
        keywords.extend(parse_keyword_file(f))

    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root_hint": str(ROOT.name),
        "scanned_paths": [str(Path(d).as_posix()) for d in SCAN_DIRS],
        "keyword_count": len(keywords),
        "keywords": keywords,
    }

    out_path = ROOT / OUTPUT_FILE
    out_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(keywords)} keywords to {out_path}")


if __name__ == "__main__":
    main()
