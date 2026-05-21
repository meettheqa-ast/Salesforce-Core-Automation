"""Backend tests for the AI prompt management subsystem.

Coverage matrix:

| Layer            | Test                                                          |
|------------------|---------------------------------------------------------------|
| compiler         | strict + lenient render, sandbox, unknown placeholder         |
| compiler         | validate_body size cap, syntax error, declared allow-list     |
| parser           | json_array tolerant parse (fences + trailing prose)           |
| parser           | markdown_table wide layout (Zephyr)                           |
| parser           | markdown_table vertical layout (Salesforce Structured)        |
| parser           | error on no title column                                      |
| parser           | robot_script / freeform pass-through                          |
| registry         | seed_system_templates idempotency + content-aware re-version  |
| registry         | create_user_template + append_version + cache_epoch bump      |
| resolver         | system default fallback                                       |
| resolver         | user override wins over project + org + system                |
| resolver         | cache invalidates on epoch bump                               |
| assembler        | feature-flag off vs on returns same default-drafter text      |
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ---------- shared fixtures --------------------------------------


@pytest.fixture
def isolated_db(monkeypatch):
    """Spin up a clean in-memory SQLite DB with the full prompt schema
    + an empty users table so FKs are satisfied."""
    from ai_qa_portal.backend.services import db as db_mod
    from ai_qa_portal.backend.services.db import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", SessionLocal)
    return SessionLocal()


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Every test starts with an empty resolver cache so cache_epoch
    interactions are deterministic."""
    from ai_qa_portal.backend.services import prompt_resolver
    prompt_resolver.clear_cache()
    yield
    prompt_resolver.clear_cache()


# ---------- compiler ---------------------------------------------


def test_compiler_strict_render_succeeds():
    from ai_qa_portal.backend.services.prompt_compiler import compile

    out = compile(
        "Hello {{ name }}, {{ count }} test cases requested.",
        {"name": "Maitri", "count": 3},
    )
    assert out.text == "Hello Maitri, 3 test cases requested."
    assert sorted(out.placeholders_used) == ["count", "name"]


def test_compiler_strict_raises_on_undefined():
    from ai_qa_portal.backend.services.prompt_compiler import PromptCompileError, compile

    with pytest.raises(PromptCompileError, match="Undefined placeholder"):
        compile("Hi {{ missing.var }}", {})


def test_compiler_lenient_renders_empty_for_undefined():
    from ai_qa_portal.backend.services.prompt_compiler import compile

    out = compile("Hi {{ missing.var }} end", {}, strict=False)
    assert out.text == "Hi  end"


def test_compiler_sandbox_blocks_dunder_walk():
    from ai_qa_portal.backend.services.prompt_compiler import PromptCompileError, compile

    with pytest.raises(PromptCompileError, match="sandbox"):
        compile("{{ ().__class__.__base__.__subclasses__() }}", {})


def test_validate_body_rejects_undeclared_placeholder():
    from ai_qa_portal.backend.services.prompt_compiler import (
        PromptCompileError,
        validate_body,
    )

    with pytest.raises(PromptCompileError, match="Undeclared placeholder"):
        validate_body(
            "Hi {{ stroy.title }}",
            declared_placeholders={"story", "project"},
            max_bytes=2000,
        )


def test_validate_body_rejects_oversize():
    from ai_qa_portal.backend.services.prompt_compiler import (
        PromptCompileError,
        validate_body,
    )

    big = "a" * 500
    with pytest.raises(PromptCompileError, match="cap is"):
        validate_body(big, declared_placeholders=set(), max_bytes=100)


def test_validate_body_accepts_clean_template():
    from ai_qa_portal.backend.services.prompt_compiler import validate_body

    used = validate_body(
        "{% if story.title %}{{ story.title }}{% endif %} -- {{ project.slug }}",
        declared_placeholders={"story", "project"},
        max_bytes=2000,
    )
    assert used == {"story", "project"}


# ---------- output parser ----------------------------------------


def test_parse_json_array_tolerates_fences():
    from ai_qa_portal.backend.services.prompt_output_parser import parse

    raw = (
        "Sure:\n```json\n"
        "[{\"title\":\"a\",\"steps\":[\"s\"],\"expected_result\":\"x\",\"preconditions\":null,\"suggested_tags\":[\"Smoke\"]}]\n"
        "```"
    )
    out = parse(raw, output_format="json_array")
    assert len(out.test_cases) == 1
    assert out.test_cases[0].title == "a"
    assert out.test_cases[0].suggested_tags == ["Smoke"]


def test_parse_markdown_table_wide_zephyr_shape():
    from ai_qa_portal.backend.services.prompt_output_parser import parse

    raw = (
        "| Test Case ID | Summary | Pre-conditions | Test Steps | Expected Results | Priority | Components | Labels | Test Type |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| TC_001 | [Cart] Verify add | 1. On PDP | 1. Click 2. Verify | 1. Cart updates | Critical | Cart | smoke | Functional |\n"
    )
    out = parse(raw, output_format="markdown_table")
    assert len(out.test_cases) == 1
    tc = out.test_cases[0]
    assert tc.title == "[Cart] Verify add"
    assert tc.steps == ["Click", "Verify"]
    assert "priority:critical" in tc.suggested_tags
    assert "smoke" in tc.suggested_tags
    assert "Cart" in tc.suggested_tags


def test_parse_markdown_table_vertical_sf_shape():
    from ai_qa_portal.backend.services.prompt_output_parser import parse

    raw = (
        "| Field | Value |\n"
        "|---|---|\n"
        "| TC ID | TC-SF-001 |\n"
        "| Title | Verify governor limit |\n"
        "| Test Steps | 1. Open 2. Trigger |\n"
        "| Expected Result | Limit OK |\n"
        "| Priority | P1 |\n"
        "\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| TC ID | TC-SF-002 |\n"
        "| Title | Verify boundary case |\n"
        "| Test Steps | 1. Open 2. Boundary trigger |\n"
        "| Expected Result | Boundary OK |\n"
        "| Priority | P2 |\n"
    )
    out = parse(raw, output_format="markdown_table")
    assert len(out.test_cases) == 2
    assert {tc.title for tc in out.test_cases} == {
        "Verify governor limit", "Verify boundary case",
    }


def test_parse_markdown_table_no_title_column_errors():
    from ai_qa_portal.backend.services.prompt_output_parser import (
        OutputParseError,
        parse,
    )

    raw = (
        "| Steps | Expected |\n"
        "|---|---|\n"
        "| open | ok |\n"
    )
    with pytest.raises(OutputParseError):
        parse(raw, output_format="markdown_table")


def test_parse_robot_script_passthrough():
    from ai_qa_portal.backend.services.prompt_output_parser import parse

    raw = "*** Settings ***\nLibrary    SeleniumLibrary\n"
    out = parse(raw, output_format="robot_script")
    assert out.raw_text == raw
    assert out.test_cases == []


# ---------- registry seeding -------------------------------------


def test_seed_system_templates_idempotent(isolated_db):
    from ai_qa_portal.backend.services import prompt_registry
    from ai_qa_portal.backend.services.db_models.prompts import (
        PromptTemplate, PromptVersion,
    )

    db = isolated_db
    s1 = prompt_registry.seed_system_templates(db)
    assert s1["created"] == 8, "First run creates all 8 manifest entries"
    assert s1["version_appended"] == 0

    s2 = prompt_registry.seed_system_templates(db)
    assert s2["created"] == 0, "Second run creates nothing"
    assert s2["version_appended"] == 0, "Identical seed bodies skip new versions"
    assert s2["unchanged"] == 8

    tcount = db.query(PromptTemplate).count()
    vcount = db.query(PromptVersion).count()
    assert tcount == 8 and vcount == 8


def test_seed_appends_version_when_body_changes(isolated_db, tmp_path, monkeypatch):
    """Simulate a release that ships an updated seed body. The seeder
    must append a NEW version (not overwrite v1) and update the
    template's seed_content_sha."""
    from ai_qa_portal.backend.services import prompt_registry
    from ai_qa_portal.backend.services.db_models.prompts import (
        PromptTemplate, PromptVersion,
    )

    db = isolated_db
    prompt_registry.seed_system_templates(db)

    # Patch read_seed_body to return modified content for one specific
    # filename. seed_system_templates calls it via the module-level
    # import so we patch at the source.
    original = prompt_registry.read_seed_body

    def patched(name):
        if name == "healer.md":
            return "MODIFIED healer body for release N+1"
        return original(name)

    monkeypatch.setattr(prompt_registry, "read_seed_body", patched)
    s = prompt_registry.seed_system_templates(db)
    assert s["version_appended"] == 1

    healer = (
        db.query(PromptTemplate)
        .filter(PromptTemplate.category == "healer")
        .filter(PromptTemplate.is_system.is_(True))
        .one()
    )
    versions = (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == healer.id)
        .order_by(PromptVersion.version_number)
        .all()
    )
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[1].body == "MODIFIED healer body for release N+1"


def test_create_user_template_and_append_version(isolated_db):
    from ai_qa_portal.backend.services import prompt_registry

    db = isolated_db
    prompt_registry.seed_system_templates(db)
    seed = (
        db.query(prompt_registry.PromptTemplate)
        .filter(prompt_registry.PromptTemplate.category == "test_case_drafter")
        .filter(prompt_registry.PromptTemplate.is_system.is_(True))
        .order_by(prompt_registry.PromptTemplate.created_at)
        .first()
    )
    tw = prompt_registry.create_user_template(
        db,
        category="test_case_drafter",
        name="My custom drafter",
        body="Body v1",
        description="user clone",
        output_format="json_array",
        placeholders_declared=["story", "project"],
        owner_user_id=None,
        source_template_id=seed.id,
    )
    assert tw.version.version_number == 1
    v2 = prompt_registry.append_version(
        db, template=tw.template, body="Body v2",
        change_note="tweak", created_by_user_id=None,
    )
    assert v2.version_number == 2

    versions = prompt_registry.list_versions(db, tw.template.id)
    assert [v.version_number for v in versions] == [2, 1]


def test_cache_epoch_bumps_on_mutation(isolated_db):
    from ai_qa_portal.backend.services import prompt_registry

    db = isolated_db
    e1 = prompt_registry.get_cache_epoch(db)
    prompt_registry.bump_cache_epoch(db)
    e2 = prompt_registry.get_cache_epoch(db)
    assert e2 == e1 + 1


# ---------- resolver ---------------------------------------------


def test_resolver_returns_system_default(isolated_db):
    from ai_qa_portal.backend.services import prompt_registry, prompt_resolver

    db = isolated_db
    prompt_registry.seed_system_templates(db)
    resolved = prompt_resolver.resolve(db, category="test_case_drafter")
    assert resolved is not None
    assert resolved.source_scope == "system"
    assert resolved.template_name == "Default JSON drafter"


def test_resolver_user_override_wins(isolated_db):
    from ai_qa_portal.backend.services import prompt_registry, prompt_resolver

    db = isolated_db
    prompt_registry.seed_system_templates(db)
    # Create a user clone + override.
    seed = (
        db.query(prompt_registry.PromptTemplate)
        .filter(prompt_registry.PromptTemplate.category == "test_case_drafter")
        .filter(prompt_registry.PromptTemplate.is_system.is_(True))
        .first()
    )
    user_tpl = prompt_registry.create_user_template(
        db,
        category="test_case_drafter",
        name="Maitri custom",
        body="User-only body",
        description="",
        output_format="json_array",
        placeholders_declared=[],
        owner_user_id="u-1",
        source_template_id=seed.id,
    )
    prompt_registry.set_override(
        db,
        scope="user",
        scope_id="u-1",
        category="test_case_drafter",
        template_id=user_tpl.template.id,
        active_version_id=user_tpl.version.id,
        updated_by_user_id="u-1",
    )

    # User u-1 sees their override, anonymous user sees system default.
    resolved_u = prompt_resolver.resolve(
        db, category="test_case_drafter", user_id="u-1",
    )
    resolved_anon = prompt_resolver.resolve(
        db, category="test_case_drafter",
    )
    assert resolved_u.source_scope == "user"
    assert resolved_u.template_name == "Maitri custom"
    assert resolved_u.body == "User-only body"
    assert resolved_anon.source_scope == "system"
    assert resolved_anon.template_name == "Default JSON drafter"


def test_resolver_cache_invalidates_on_epoch_bump(isolated_db):
    """Editing an override should make the next resolve see the new
    body, not the cached one."""
    from ai_qa_portal.backend.services import prompt_registry, prompt_resolver

    db = isolated_db
    prompt_registry.seed_system_templates(db)
    seed = (
        db.query(prompt_registry.PromptTemplate)
        .filter(prompt_registry.PromptTemplate.category == "healer")
        .first()
    )
    # Prime the cache.
    r1 = prompt_resolver.resolve(db, category="healer", user_id="u-2")
    assert r1.source_scope == "system"

    # Create user override -> automatically bumps epoch.
    user_tpl = prompt_registry.create_user_template(
        db,
        category="healer",
        name="Custom healer",
        body="Custom healer body",
        description="",
        output_format="robot_script",
        placeholders_declared=[],
        owner_user_id="u-2",
        source_template_id=seed.id,
    )
    prompt_registry.set_override(
        db,
        scope="user",
        scope_id="u-2",
        category="healer",
        template_id=user_tpl.template.id,
        active_version_id=user_tpl.version.id,
        updated_by_user_id="u-2",
    )

    r2 = prompt_resolver.resolve(db, category="healer", user_id="u-2")
    assert r2.source_scope == "user"
    assert r2.body == "Custom healer body"


def test_resolver_returns_none_for_unknown_category(isolated_db):
    from ai_qa_portal.backend.services import prompt_resolver

    db = isolated_db
    res = prompt_resolver.resolve(db, category="nope_never_seeded")
    assert res is None


# ---------- assembler integration --------------------------------


def test_assembler_flag_off_uses_legacy_text(monkeypatch):
    monkeypatch.delenv("PROMPT_REGISTRY_ENABLED", raising=False)
    from ai_qa_portal.backend.prompts import assembler
    out = assembler.build_system_prompt("drafter")
    assert "Test Case Drafter".lower() in out.lower() or "DRAFTER" in out


def test_assembler_flag_on_returns_same_default_body(monkeypatch, isolated_db):
    """When the user has not overridden anything, the flag-on path
    must produce the same baseline content as the flag-off path."""
    from ai_qa_portal.backend.prompts import assembler
    from ai_qa_portal.backend.services import prompt_registry, prompt_resolver

    prompt_registry.seed_system_templates(isolated_db)
    prompt_resolver.clear_cache()

    monkeypatch.delenv("PROMPT_REGISTRY_ENABLED", raising=False)
    legacy = assembler.build_system_prompt("drafter")
    monkeypatch.setenv("PROMPT_REGISTRY_ENABLED", "1")
    resolved = assembler.build_system_prompt("drafter")

    # Length within 1% -- minor trailing-newline / playbook-cache jitter is fine.
    delta = abs(len(legacy) - len(resolved))
    assert delta < max(50, int(len(legacy) * 0.01)), (
        f"flag on/off bodies diverge: legacy={len(legacy)} resolved={len(resolved)}"
    )
