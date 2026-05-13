"""Robot ``--dryrun`` gate for LLM-generated scripts.

Why a dryrun on top of the AST validator: the AST validator catches
hallucinated symbols, but it deliberately does not check argument arity
or import-time side effects (resource files that themselves fail to
load, libraries that raise at import, etc.). ``robot --dryrun`` does
exactly that — it parses everything, resolves every keyword call, and
checks the call signatures, **without executing any keyword body**. So
running it after the AST gate catches things like
``Select Random Dropdown Option In Modal`` being called with one arg
when the keyword takes none.

This module spawns Robot in a subprocess (so a botched suite cannot
poison the caller's interpreter), captures stdout+stderr, and parses
the standard ``[ ERROR ]`` / ``| FAIL |`` lines into the same
:class:`ValidationError` shape produced by ``script_validator``. The
retry loop in ``test_case_script_builder`` consumes both the same way.

Performance: cold dryrun on this codebase is ~2 s. The runner is
expected to be called at most once per LLM round-trip after the AST
gate has already passed, so the marginal cost is acceptable. The
subprocess uses the venv interpreter the backend is running under so
no PATH lookup happens at runtime.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_qa_portal.backend.services.script_validator import ValidationError, ValidationReport

logger = logging.getLogger("ai_qa_portal.script_dryrun")


# Robot console output formats we recognise. Examples of what we parse:
#
#   [ ERROR ] Error in file 'C:\path\suite.robot' on line 14: Setting variable
#       '${x}' failed: Variable '${RANDOM_STRING}' not found.
#
#   [ ERROR ] Resource file 'doesnotexist.robot' does not exist.
#
#   <Test Name> | FAIL |
#   Keyword 'Foo.Bar' expected 0 arguments, got 1.
#
# The first form is the rich one — file, line, reason all in a single
# bracketed line. The second form (FAIL) is two physical lines: status
# line then reason line. We assemble both into structured errors.
_ERROR_LINE_RE = re.compile(
    r"^\[\s*ERROR\s*\]\s+(?:Error in file '(?P<file>[^']+)' on line (?P<line>\d+):\s+)?(?P<rest>.+)$"
)
_FAIL_HEADER_RE = re.compile(r"\|\s*FAIL\s*\|\s*$")
# Robot's end-of-suite summary line ("N tests, N passed, N failed") looks
# like a reason continuation but is not. Same for the elapsed-time line
# ("Elapsed time: ...") and the output-file path line ("Output: ...").
# Filtering these here keeps the parser's error list signal-only.
_NOISE_LINE_RES = (
    re.compile(r"^\d+\s+tests?,\s+\d+\s+passed,\s+\d+\s+failed", re.IGNORECASE),
    re.compile(r"^Elapsed time:", re.IGNORECASE),
    re.compile(r"^(Output|Log|Report):", re.IGNORECASE),
    re.compile(r"^[=]{5,}$"),
)


def _classify(message: str) -> tuple[str, str]:
    """Heuristically map a Robot error message to (kind, symbol).

    Robot's own error strings are not machine-readable, but they are
    consistent enough that a few targeted patterns extract the symbol
    we want to feed back into the LLM fix prompt.
    """
    # "No keyword with name 'GlobalApi.API Seed Campaign' found."
    m = re.search(r"No keyword with name '([^']+)' found", message)
    if m:
        return "undefined_keyword", m.group(1)

    # "Variable '${X}' not found."
    m = re.search(r"Variable '([^']+)' not found", message)
    if m:
        return "undefined_variable", m.group(1)

    # "Resource file 'foo.robot' does not exist."
    m = re.search(r"Resource file '([^']+)' does not exist", message)
    if m:
        return "missing_resource", m.group(1)

    # "Importing library 'X' failed: ..."
    m = re.search(r"Importing library '([^']+)' failed", message)
    if m:
        return "missing_library", m.group(1)

    # "Keyword '<X>' expected N arguments, got M."
    m = re.search(r"Keyword '([^']+)' expected (\d+|no) arguments?, got (\d+)", message)
    if m:
        return "undefined_keyword", m.group(1)  # "wrong arity" surfaces as a keyword issue

    return "parse_error", message[:80].strip()


def _parse_console(text: str, suite_path: Path) -> list[ValidationError]:
    """Walk the captured console output and emit one ``ValidationError`` per
    Robot error message. Carries ``file`` / ``line`` when Robot included
    them; otherwise emits a file-level error."""
    errors: list[ValidationError] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _ERROR_LINE_RE.match(line.strip())
        if m:
            line_no = int(m.group("line")) if m.group("line") else 0
            rest = m.group("rest").strip()
            kind, symbol = _classify(rest)
            errors.append(ValidationError(
                line=line_no,
                column=0,
                kind=kind,  # type: ignore[arg-type]
                symbol=symbol,
                message=rest,
                snippet=_line_text(suite_path, line_no),
            ))
            i += 1
            continue

        # FAIL pattern: a "| FAIL |" header line followed by one or more
        # description lines until the next dashed separator or section
        # header. Reason text often spans 1-3 physical lines. Suite
        # summary lines ("N tests, N passed, N failed", "Elapsed time:",
        # "Output:") are filtered as noise so they don't end up in the
        # reason.
        if _FAIL_HEADER_RE.search(line):
            j = i + 1
            reason_parts: list[str] = []
            while j < len(lines):
                nxt = lines[j].strip()
                if (not nxt
                        or nxt.startswith("---")
                        or nxt.startswith("==")
                        or "| PASS |" in nxt
                        or "| FAIL |" in nxt):
                    break
                if any(p.match(nxt) for p in _NOISE_LINE_RES):
                    break
                reason_parts.append(nxt)
                j += 1
            reason = " ".join(reason_parts).strip()
            if reason:
                kind, symbol = _classify(reason)
                errors.append(ValidationError(
                    line=0,
                    column=0,
                    kind=kind,  # type: ignore[arg-type]
                    symbol=symbol,
                    message=reason,
                    snippet="",
                ))
            i = j
            continue

        i += 1
    return errors


def _line_text(suite_path: Path, line: int) -> str:
    if not suite_path.is_file() or line <= 0:
        return ""
    try:
        text = suite_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    rows = text.splitlines()
    if 1 <= line <= len(rows):
        return rows[line - 1].rstrip()
    return ""


def dryrun(
    suite_path: Path | str,
    *,
    timeout: float = 30.0,
    python_executable: str | None = None,
) -> ValidationReport:
    """Run ``robot --dryrun`` against ``suite_path`` and parse the results.

    Returns a ``ValidationReport`` whose ``errors`` is empty when the
    suite parses cleanly. ``ok`` is True iff (a) Robot exited 0, and
    (b) we did not detect any ``[ ERROR ]`` / ``| FAIL |`` markers in
    the console output. Both conditions matter because Robot returns 0
    on suite-level failures (a single test failing is still a clean
    suite exit when there are no syntax errors).
    """
    path = Path(suite_path)
    if not path.is_file():
        return ValidationReport(
            ok=False,
            errors=[ValidationError(
                line=0, column=0, kind="parse_error",
                symbol=str(path), message=f"Script not found at {path}",
            )],
        )

    py = python_executable or sys.executable
    # We pass an ABSOLUTE suite path and do NOT override ``cwd`` -- the
    # subprocess inherits the parent's cwd. This avoids a subtle bug
    # where Robot would double-resolve a relative suite path against a
    # cwd we'd just changed (e.g. ``Tests\SmokeTests\Tests\SmokeTests\...``).
    # ``--output NONE`` (special token) disables Robot's output XML
    # without us having to manage temp files.
    abs_path = path.resolve()
    cmd = [
        py, "-m", "robot",
        "--dryrun",
        "--output", "NONE",
        "--report", "NONE",
        "--log", "NONE",
        str(abs_path),
    ]
    with tempfile.TemporaryDirectory(prefix="rfdryrun-") as _td:  # noqa: F841
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ValidationReport(
                ok=False,
                errors=[ValidationError(
                    line=0, column=0, kind="parse_error",
                    symbol="timeout",
                    message=f"robot --dryrun timed out after {timeout}s",
                )],
            )
        except OSError as exc:
            return ValidationReport(
                ok=False,
                errors=[ValidationError(
                    line=0, column=0, kind="parse_error",
                    symbol="os_error",
                    message=f"could not start robot --dryrun: {exc}",
                )],
            )

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    errors = _parse_console(combined, path)
    # Robot occasionally exits non-zero with no parse-able errors (e.g.
    # missing parser plugins). Surface the raw tail so the LLM at least
    # sees what went wrong.
    if proc.returncode != 0 and not errors:
        tail = combined.strip().splitlines()[-3:]
        errors.append(ValidationError(
            line=0, column=0, kind="parse_error",
            symbol=f"exit {proc.returncode}",
            message="robot --dryrun exited non-zero: " + " | ".join(tail),
        ))

    return ValidationReport(ok=not errors, errors=errors)
