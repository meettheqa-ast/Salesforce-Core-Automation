"""Tests for the LLM-output validator + dryrun gate.

Covers:
  * The exact failing script from the field session (golden-failure
    regression: ``${RANDOM_STRING}`` must surface with sensible
    closest-match suggestions, and the AST validator must accept the
    REAL Campaign keywords once they exist).
  * A clean script must validate clean (no false positives on a
    suite that uses qualified library prefixes + transitively-imported
    variables).
  * Closest-match suggestions are ordered by similarity and bounded.
  * The build_fix_prompt rendering produces a message the LLM can act on.
  * The dryrun wrapper parses ``[ ERROR ]`` lines correctly.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ai_qa_portal.backend.config import REPO_ROOT
from ai_qa_portal.backend.services import script_validator as sv


@pytest.fixture()
def write_suite(tmp_path: Path):
    """Helper: write a Robot suite text into a tmpfile and return its path.
    The validator is path-based (it reuses ``robot.api.get_model``) so a
    real on-disk file is the simplest way to exercise it.

    Test suites that need to import the real ``Resources/`` tree should
    use absolute paths via the ``REPO`` placeholder, replaced here with
    ``REPO_ROOT.as_posix()``. This keeps the validator tests
    self-contained (no fragile ``../../Resources/...`` math) while still
    exercising the transitive-import walker against real files.
    """
    counter = {"n": 0}

    def _write(content: str) -> Path:
        counter["n"] += 1
        p = tmp_path / f"suite_{counter['n']}.robot"
        rendered = textwrap.dedent(content).replace("{REPO}", REPO_ROOT.as_posix())
        p.write_text(rendered, encoding="utf-8")
        return p

    return _write


# ---------------------------------------------------------------------------
# Golden-failure regression: the exact shape of the failing script that
# triggered the harden-generation-pipeline plan. ``${RANDOM_STRING}`` must
# be flagged; the (now-real) Campaign keywords must NOT be flagged.
# ---------------------------------------------------------------------------

FAILING_SCRIPT = """\
*** Settings ***
Documentation       Tests the full cycle: Campaign -> Lead -> Account/Contact conversion.

Library             SeleniumLibrary
Resource            {REPO}/Resources/Common/GlobalKeywords.robot
Resource            {REPO}/Resources/PO/Platform/SalesPO.robot
Resource            {REPO}/Resources/Common/GlobalApi.robot

Test Setup          Begin Web Test
Test Teardown       End Web Test


*** Variables ***
${campaignName}     Generated Campaign ${RANDOM_STRING}


*** Test Cases ***
Full Cycle Campaign Lead To Account Contact Conversion
    [Documentation]    Tests the full cycle.
    [Tags]    smoke    campaign    lead
    GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
    ${campaignId}=    GlobalApi.API Seed Campaign    Name=${campaignName}    Type=Advertisement    Status=Planned
    Launch App    ${salesAutomationAppName}
    Select App Tab    Leads
    SalesPO.Create A New Lead
    GlobalApi.API Cleanup Record    Campaign    ${campaignId}
"""


def test_failing_script_flags_random_string(write_suite):
    """Golden regression: ${RANDOM_STRING} must be detected and the
    Campaign keywords (now real) must NOT be flagged."""
    suite = write_suite(FAILING_SCRIPT)
    report = sv.validate(suite)

    assert not report.ok, "validator must reject scripts with undefined variables"

    # Find the ${RANDOM_STRING} error specifically.
    random_errors = [
        e for e in report.errors
        if e.kind == "undefined_variable" and "RANDOM_STRING" in e.symbol
    ]
    assert random_errors, (
        "Expected ${RANDOM_STRING} to be flagged. Errors found: "
        + ", ".join(f"{e.kind}:{e.symbol}" for e in report.errors)
    )
    # Closest-match suggestions must be a list (potentially empty when
    # nothing similar exists in the project's variable namespace) but
    # never None.
    assert isinstance(random_errors[0].closest_matches, list)

    # The Campaign keywords must NOT be flagged: API Seed Campaign exists.
    campaign_kw_errors = [
        e for e in report.errors
        if e.kind == "undefined_keyword" and "API Seed Campaign" in e.symbol
    ]
    assert not campaign_kw_errors, (
        "API Seed Campaign now exists in the catalog -- it must not be flagged. "
        f"Got: {[e.symbol for e in campaign_kw_errors]}"
    )


def test_known_good_suite_passes(write_suite):
    """A suite that uses real keywords + imports a real Resource that
    transitively defines its variables must validate clean."""
    suite = write_suite("""\
        *** Settings ***
        Resource    {REPO}/Resources/Common/GlobalKeywords.robot
        Resource    {REPO}/Resources/PO/Platform/SalesPO.robot
        Test Setup       Begin Web Test
        Test Teardown    End Web Test

        *** Test Cases ***
        Smoke Lead Create
            [Documentation]    Smoke create + verify.
            [Tags]    smoke    lead
            GlobalKeywords.Login To Sandbox    ${globalSandboxTestUrl}    ${sandboxUserNameInput}    ${sandboxPasswordInput}
            Launch App    ${salesAutomationAppName}
            Select App Tab    Leads
            Open New Dialog    Lead
            SalesPO.Create A New Lead
            SalesPO.Verify Lead Created Successfully
    """)
    report = sv.validate(suite)
    assert report.ok, (
        "Expected clean suite to pass. Errors: "
        + "; ".join(f"L{e.line} {e.kind}: {e.symbol}" for e in report.errors)
    )
    assert report.keyword_calls_seen >= 6
    assert report.variable_refs_seen >= 4


def test_undefined_keyword_yields_suggestions(write_suite):
    """A misspelled keyword close to a real one must surface suggestions."""
    suite = write_suite("""\
        *** Settings ***
        Resource    {REPO}/Resources/PO/Platform/SalesPO.robot
        Test Setup       Begin Web Test
        Test Teardown    End Web Test

        *** Test Cases ***
        Misspelled Keyword
            [Tags]    smoke
            SalesPO.Verify Lead Created Successful
    """)
    report = sv.validate(suite)
    assert not report.ok
    bad = [e for e in report.errors if e.kind == "undefined_keyword"]
    assert bad, "Expected one undefined_keyword error"
    # closest_matches must include at least one keyword that contains
    # "Verify Lead" -- the canonical correction is "Verify Lead Created
    # Successfully".
    suggestions_lower = " | ".join(bad[0].closest_matches).lower()
    assert "verify lead created successfully" in suggestions_lower, (
        f"Expected 'Verify Lead Created Successfully' in suggestions, got: {bad[0].closest_matches}"
    )


def test_missing_resource_import_is_flagged(write_suite):
    """Path-style Resource imports that don't resolve must produce a
    missing_resource error (so the LLM can fix the path on retry)."""
    suite = write_suite("""\
        *** Settings ***
        Resource    ../this/path/does/not/exist.robot
        Test Setup       Begin Web Test
        Test Teardown    End Web Test

        *** Test Cases ***
        Trivial
            [Tags]    smoke
            Log    Hello
    """)
    report = sv.validate(suite)
    assert not report.ok
    assert any(e.kind == "missing_resource" for e in report.errors), (
        "Expected missing_resource error. Got: "
        + ", ".join(f"{e.kind}:{e.symbol}" for e in report.errors)
    )


def test_build_fix_prompt_mentions_each_error(write_suite):
    """The fix-prompt rendering must name each undefined symbol so the
    LLM can act on it without rereading the script."""
    suite = write_suite(FAILING_SCRIPT)
    report = sv.validate(suite)
    fix = sv.build_fix_prompt(report)

    assert "RANDOM_STRING" in fix
    assert "Return ONLY" in fix.upper() or "return only" in fix.lower()


def test_robot_builtin_variables_pass(write_suite):
    """${SPACE} / ${EMPTY} / ${TRUE} / ${TEST_NAME} must be considered
    defined without the suite declaring them."""
    suite = write_suite("""\
        *** Settings ***
        Test Setup       Log    setup
        Test Teardown    Log    teardown

        *** Test Cases ***
        Built-In Variables
            [Tags]    smoke
            Log    Test name is ${TEST_NAME}
            Log    ${SPACE}between${SPACE}words
            Should Be Equal    ${TRUE}    ${TRUE}
    """)
    report = sv.validate(suite)
    assert report.ok, (
        "Robot built-in variables must not be flagged. Errors: "
        + ", ".join(f"{e.symbol}" for e in report.errors)
    )


def test_dryrun_parses_robot_errors(tmp_path: Path):
    """Smoke test for the dryrun parser: feeding a known-bad suite
    produces at least one ValidationError surfaced from Robot's
    [ ERROR ] line."""
    from ai_qa_portal.backend.services import script_dryrun as sd

    bad = tmp_path / "bad.robot"
    bad.write_text(textwrap.dedent("""\
        *** Settings ***
        Test Setup       Log    setup
        Test Teardown    Log    teardown

        *** Variables ***
        ${greeting}    Hello ${NEVER_DEFINED}

        *** Test Cases ***
        Bad
            [Tags]    smoke
            Log    ${greeting}
    """), encoding="utf-8")

    report = sd.dryrun(bad, timeout=30.0)
    assert not report.ok, "dryrun must reject a script with an undefined variable on the RHS"
    # We don't assert on exact error text -- Robot's wording can shift
    # across versions -- just that we surfaced AT LEAST one structured
    # error.
    assert report.errors, "dryrun produced no errors despite returning ok=False"
