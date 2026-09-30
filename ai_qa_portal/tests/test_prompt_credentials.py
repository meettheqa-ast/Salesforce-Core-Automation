"""Tests for prompt-embedded credential extraction."""

from __future__ import annotations

from ai_qa_portal.backend.services.prompt_credentials import extract_credentials_from_prompt


def test_labeled_username_and_password():
    prompt = (
        "Create a Lead as test user.\n"
        "Username: qa.lead@example.com\n"
        "Password: S3cret!pass\n"
    )
    assert extract_credentials_from_prompt(prompt) == (
        "qa.lead@example.com",
        "S3cret!pass",
    )


def test_combined_as_user_with_password():
    prompt = "Login as user seller@acme.test with password Winter2026!"
    assert extract_credentials_from_prompt(prompt) == (
        "seller@acme.test",
        "Winter2026!",
    )


def test_missing_password_returns_none():
    prompt = "Use username: only.user@example.com to create an Account"
    assert extract_credentials_from_prompt(prompt) is None


def test_missing_username_returns_none():
    prompt = "Password: somethingSecret create a Lead"
    assert extract_credentials_from_prompt(prompt) is None


def test_workspace_only_prompt_returns_none():
    prompt = "Create a single Lead and verify the success toast."
    assert extract_credentials_from_prompt(prompt) is None


def test_placeholder_password_ignored():
    prompt = "Username: real.user@example.com\nPassword: ${sandboxPasswordInput}"
    assert extract_credentials_from_prompt(prompt) is None


def test_quoted_values():
    prompt = 'test account: "ops@example.com"\npassword: "p@ssWord"'
    assert extract_credentials_from_prompt(prompt) == ("ops@example.com", "p@ssWord")
