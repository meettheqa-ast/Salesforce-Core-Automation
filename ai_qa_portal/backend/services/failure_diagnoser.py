"""Parse a failed Robot Framework run into a structured diagnosis.

The diagnoser walks `output.xml` for a single run folder and surfaces:
  - The first failing test name.
  - The first failing keyword inside it (with arg list + error message).
  - The last selenium screenshot Robot dropped during that test (if any).
  - The full failure trail for the LLM to reason over.

Output is intentionally LLM-friendly: short strings, no XML, no Robot
internals. The healer endpoint feeds it directly into the prompt.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ai_qa_portal.failure_diagnoser")


@dataclass
class KeywordFailure:
    keyword_name: str
    arguments: list[str] = field(default_factory=list)
    error_message: str = ""
    # Dot-path of ancestor keywords leading to this one, top-down. Helps
    # the LLM understand whether the failing call was a primitive or a
    # composite (e.g. `Set Picklist Field > Open Dropdown` failing means
    # the picklist label is wrong; raw `Click Element` failing means the
    # AI fell back to a primitive when a recipe existed).
    call_path: list[str] = field(default_factory=list)


@dataclass
class RunDiagnosis:
    run_folder: str
    test_name: str
    test_status: str  # "PASS" / "FAIL" / "SKIP" / "NOT_RUN"
    test_message: str
    first_failure: Optional[KeywordFailure]
    all_failures: list[KeywordFailure]
    screenshot_path: Optional[str]  # absolute path on disk

    def to_dict(self) -> dict:
        return {
            "run_folder": self.run_folder,
            "test_name": self.test_name,
            "test_status": self.test_status,
            "test_message": self.test_message,
            "first_failure": (
                {
                    "keyword_name": self.first_failure.keyword_name,
                    "arguments": list(self.first_failure.arguments),
                    "error_message": self.first_failure.error_message,
                    "call_path": list(self.first_failure.call_path),
                }
                if self.first_failure
                else None
            ),
            "all_failures": [
                {
                    "keyword_name": f.keyword_name,
                    "arguments": list(f.arguments),
                    "error_message": f.error_message,
                    "call_path": list(f.call_path),
                }
                for f in self.all_failures
            ],
            "screenshot_path": self.screenshot_path,
        }


def _kw_status(kw_el: ET.Element) -> tuple[str, str]:
    """Return (status, message) for a Robot output.xml <kw> element."""
    status_el = kw_el.find("status")
    if status_el is None:
        return "", ""
    return status_el.attrib.get("status", ""), (status_el.text or "").strip()


def _kw_args(kw_el: ET.Element) -> list[str]:
    """Robot >=4 puts args under <arg> children of <arguments>; older
    versions used <arg> directly inside <kw>. Handle both."""
    args: list[str] = []
    args_el = kw_el.find("arguments")
    if args_el is not None:
        for a in args_el.findall("arg"):
            args.append((a.text or "").strip())
    else:
        for a in kw_el.findall("arg"):
            args.append((a.text or "").strip())
    return args


def _walk_for_failures(
    kw_el: ET.Element,
    out: list[KeywordFailure],
    path: list[str],
) -> None:
    """Depth-first walk; append every keyword whose own status is FAIL.

    The first leaf failure tends to be the actionable one (the LLM should
    fix THAT keyword), but composite-keyword failures are kept too so the
    healer can choose whether to replace a primitive or a whole recipe.
    """
    name = kw_el.attrib.get("name", "") or ""
    library = kw_el.attrib.get("library", "")
    qualified = f"{library}.{name}" if library else name
    here = path + [qualified]
    status, msg = _kw_status(kw_el)
    has_failing_child = False
    for child in kw_el.findall("kw"):
        before = len(out)
        _walk_for_failures(child, out, here)
        if len(out) > before:
            has_failing_child = True
    # Only record this keyword if it failed AND none of its children
    # already accounted for the failure -- avoids duplicate noise where
    # a composite shows up alongside the primitive that actually broke.
    if status == "FAIL" and not has_failing_child:
        out.append(
            KeywordFailure(
                keyword_name=qualified,
                arguments=_kw_args(kw_el),
                error_message=msg,
                call_path=here,
            )
        )


def diagnose_run(run_dir: Path) -> Optional[RunDiagnosis]:
    """Parse `<run_dir>/output.xml` and return a structured diagnosis.

    Returns None if the file is missing or unparseable; callers should
    treat that as "no actionable diagnosis" and surface a generic error.
    """
    output_xml = run_dir / "output.xml"
    if not output_xml.is_file():
        logger.warning("failure_diagnoser: no output.xml in %s", run_dir)
        return None

    try:
        tree = ET.parse(output_xml)
    except ET.ParseError as exc:
        logger.warning("failure_diagnoser: cannot parse %s: %s", output_xml, exc)
        return None

    root = tree.getroot()

    # Robot puts <test> elements anywhere under the root <suite>.
    first_failed_test: Optional[ET.Element] = None
    for test_el in root.iter("test"):
        st = test_el.find("status")
        if st is not None and st.attrib.get("status") == "FAIL":
            first_failed_test = test_el
            break
    if first_failed_test is None:
        # No failing test -- the test passed, or the runner crashed before
        # writing a status. Either way, nothing for the healer to do.
        return None

    test_name = first_failed_test.attrib.get("name", "")
    st = first_failed_test.find("status")
    test_status = st.attrib.get("status", "FAIL") if st is not None else "FAIL"
    test_message = (st.text or "").strip() if st is not None else ""

    failures: list[KeywordFailure] = []
    for kw_el in first_failed_test.findall("kw"):
        _walk_for_failures(kw_el, failures, [])

    # Newer Robot also stores teardown <kw> as a sibling -- include those.
    for kw_el in first_failed_test.findall("teardown/kw"):
        _walk_for_failures(kw_el, failures, ["[Teardown]"])

    # Last screenshot in the run dir (Robot names them
    # selenium-screenshot-<n>.png).
    screenshots = sorted(run_dir.glob("selenium-screenshot-*.png"))
    screenshot_path = str(screenshots[-1].resolve()) if screenshots else None

    return RunDiagnosis(
        run_folder=run_dir.name,
        test_name=test_name,
        test_status=test_status,
        test_message=test_message,
        first_failure=failures[0] if failures else None,
        all_failures=failures,
        screenshot_path=screenshot_path,
    )


def format_for_prompt(diag: RunDiagnosis) -> str:
    """Render a diagnosis as a compact text block for the healer prompt.

    Shape (markdown-like, no fences):

      Test "<name>" failed.
      Test message: <message>
      Failing keyword: <qualified name>
      Arguments: ["a", "b"]
      Error: <error>
      Call path: A > B > C

    Other failures (if any) are listed below in the same shape.
    """
    if not diag.first_failure:
        return f'Test "{diag.test_name}" failed but no per-keyword failure was recorded.\nTest message: {diag.test_message}'

    parts: list[str] = []
    parts.append(f'Test "{diag.test_name}" failed.')
    if diag.test_message:
        parts.append(f"Test message: {diag.test_message}")
    parts.append("")
    parts.append("Primary failure:")
    f = diag.first_failure
    parts.append(f"  Failing keyword: {f.keyword_name}")
    if f.arguments:
        parts.append(f"  Arguments: {f.arguments}")
    if f.error_message:
        parts.append(f"  Error: {f.error_message}")
    if f.call_path:
        parts.append(f"  Call path: {' > '.join(f.call_path)}")

    others = diag.all_failures[1:]
    if others:
        parts.append("")
        parts.append("Other failing keywords (for context):")
        for of in others[:5]:
            parts.append(f"  - {of.keyword_name}: {of.error_message[:160]}")

    return "\n".join(parts)
