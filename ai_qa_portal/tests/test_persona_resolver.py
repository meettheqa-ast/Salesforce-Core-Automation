"""Tests for PersonaResolver — covers all 4 priority paths + mismatch warning."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ai_qa_portal.backend.models.persona import Persona
from ai_qa_portal.backend.services.persona_resolver import PersonaResolver

PROJECT = uuid4()
ORG = uuid4()


def _persona(name: str, is_default: bool = False, pid=None, oid=None) -> Persona:
    return Persona(
        id=pid or uuid4(),
        project_id=PROJECT,
        org_id=oid or ORG,
        name=name,
        username=f"{name.lower().replace(' ', '.')}@example.com",
        encrypted_password="encrypted_placeholder",
        is_default=is_default,
    )


MARKETING = _persona("Marketing User")
SYSADMIN = _persona("System Admin")
DEFAULT = _persona("QA Engineer", is_default=True)
ALL = [MARKETING, SYSADMIN, DEFAULT]

resolver = PersonaResolver()


class TestPriority1UISelection:
    def test_explicit_ui_selection_returns_match(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead", MARKETING.id, ALL,
        )
        assert persona.id == MARKETING.id
        assert method == "ui_selection"

    def test_ui_selection_wrong_id_falls_through(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead", uuid4(), ALL,
        )
        assert method in ("org_default", "system_admin_fallback")


class TestPriority2PromptExtraction:
    def test_login_as_marketing_user(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "login as Marketing User and create a lead", None, ALL,
        )
        assert persona.name == "Marketing User"
        assert method == "prompt_extraction"

    def test_run_as_system_admin(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "run as System Admin and verify account", None, ALL,
        )
        assert persona.name == "System Admin"
        assert method == "prompt_extraction"

    def test_using_trigger_phrase(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead using Marketing User credentials", None, ALL,
        )
        assert persona.name == "Marketing User"
        assert method == "prompt_extraction"

    def test_acting_as_trigger(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "acting as QA Engineer delete the contact", None, ALL,
        )
        assert persona.name == "QA Engineer"
        assert method == "prompt_extraction"


class TestPriority3OrgDefault:
    def test_no_prompt_match_uses_org_default(self):
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead", None, ALL,
        )
        assert persona.name == "QA Engineer"
        assert persona.is_default is True
        assert method == "org_default"


class TestPriority4SystemAdminFallback:
    def test_fallback_to_system_admin_when_no_default(self):
        no_default = [
            _persona("Marketing User"),
            _persona("System Admin"),
        ]
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead", None, no_default,
        )
        assert persona.name == "System Admin"
        assert method == "system_admin_fallback"

    def test_system_administrator_variant(self):
        no_default = [_persona("System Administrator")]
        persona, method = resolver.resolve(
            PROJECT, ORG, "create a lead", None, no_default,
        )
        assert persona.name == "System Administrator"
        assert method == "system_admin_fallback"


class TestMismatchWarning:
    def test_prompt_mentions_nonexistent_persona_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            persona, method = resolver.resolve(
                PROJECT, ORG,
                "login as CEO Executive and create a lead",
                None, ALL,
            )
        assert method in ("org_default", "system_admin_fallback")
        assert any("CEO Executive" in r.message for r in caplog.records)

    def test_no_personas_at_all_raises(self):
        with pytest.raises(ValueError, match="No persona could be resolved"):
            resolver.resolve(PROJECT, ORG, "create a lead", None, [])


class TestExtractFromPrompt:
    def test_strips_trailing_punctuation(self):
        candidate = resolver._extract_from_prompt("login as Marketing User.")
        assert candidate == "Marketing User"

    def test_takes_up_to_4_words(self):
        candidate = resolver._extract_from_prompt(
            "run as Senior Marketing Manager Lead and do stuff"
        )
        assert candidate == "Senior Marketing Manager Lead"

    def test_no_trigger_returns_none(self):
        assert resolver._extract_from_prompt("create a lead") is None


class TestScopingFilter:
    def test_ignores_personas_from_other_orgs(self):
        other_org = uuid4()
        wrong_org_persona = _persona("Marketing User", oid=other_org)
        sysadmin = _persona("System Admin")
        persona, method = resolver.resolve(
            PROJECT, ORG,
            "login as Marketing User",
            None,
            [wrong_org_persona, sysadmin],
        )
        assert persona.name == "System Admin"
        assert method == "system_admin_fallback"
