"""Tests for the LLM auto-failover in ``ai_bridge.call_llm``.

Covers:

* Primary provider is preferred when it works (no failover noise).
* Quota error on the primary triggers failover to the next configured
  provider AND records a switch note for the API surface.
* Rate-limit / availability / auth errors are all classified for failover.
* Unrelated errors (prompt schema, etc.) are NOT failed over -- they
  re-raise so the caller doesn't mask real bugs.
* When every configured provider is exhausted, a clear RuntimeError is
  raised that names what was tried.
* ``LLM_FAILOVER_DISABLED=1`` restores the legacy single-provider path.
* Forcing ``provider="..."`` bypasses failover (deterministic tests).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import ai_bridge  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_failover_state(monkeypatch: pytest.MonkeyPatch):
    """Each test gets a clean exhausted-cache + notes buffer + env.

    We deliberately scrub every other provider's API key from the env so
    the failover chain only contains the two providers under test. This
    keeps the test hermetic on developer machines that have real
    OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. set system-wide -- without
    this, ``_build_failover_chain`` would happily route to a real
    provider's HTTP call mid-test.
    """
    # Wipe ALL real provider keys + the gemini-aliased GOOGLE_API_KEY.
    for env_var in [info[0] for info in ai_bridge.LLM_PROVIDERS.values()] + [
        "GOOGLE_API_KEY"
    ]:
        monkeypatch.delenv(env_var, raising=False)

    # Re-set just the two providers under test so failover has somewhere
    # to go. ``LLM_FAILOVER_ORDER`` pins the chain to [gemini, groq] so
    # the suite is independent of the dict-iteration order in
    # ``LLM_PROVIDERS``.
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FAILOVER_ORDER", "gemini,groq")
    monkeypatch.delenv("LLM_FAILOVER_DISABLED", raising=False)
    # Wipe per-process exhausted cache + thread-local notes buffer.
    with ai_bridge._provider_exhausted_lock:
        ai_bridge._provider_exhausted_until.clear()
    if hasattr(ai_bridge._provider_notes_local, "buffer"):
        ai_bridge._provider_notes_local.buffer.clear()
    yield


def _stub_callers(*, gemini, groq):
    """Patch ``_PROVIDER_CALLERS`` so each provider invocation runs the
    stubs we want without touching the real LLM SDKs. The fixture
    autoreverts because we patch the dict in place.
    """
    return patch.dict(
        ai_bridge._PROVIDER_CALLERS,
        {"gemini": gemini, "google": gemini, "groq": groq},
        clear=False,
    )


def test_primary_provider_wins_when_healthy() -> None:
    """No failover when the primary returns cleanly."""
    def gemini_ok(_s, _u, image_bytes=None):  # noqa: ARG001
        return "OK_FROM_GEMINI"

    def groq_should_not_run(_s, _u, image_bytes=None):  # noqa: ARG001
        raise AssertionError("groq must not be called when gemini works")

    with _stub_callers(gemini=gemini_ok, groq=groq_should_not_run):
        out = ai_bridge.call_llm("sys", "user")
    assert out == "OK_FROM_GEMINI"
    assert ai_bridge.drain_provider_notes() == []


def test_quota_error_triggers_failover_with_switch_note() -> None:
    """Gemini-style quota error must fall through to Groq AND surface a
    switch note for the UI."""
    def gemini_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError(
            "google.api_core.exceptions.ResourceExhausted: 429 You "
            "exceeded your current quota"
        )

    def groq_ok(_s, _u, image_bytes=None):  # noqa: ARG001
        return "OK_FROM_GROQ"

    with _stub_callers(gemini=gemini_quota, groq=groq_ok):
        out = ai_bridge.call_llm("sys", "user")
    assert out == "OK_FROM_GROQ"

    notes = ai_bridge.drain_provider_notes()
    assert len(notes) == 1
    assert notes[0]["from_provider"] == "gemini"
    assert notes[0]["to_provider"] == "groq"
    assert "quota" in notes[0]["reason"].lower()
    # User-facing label must be populated for the banner.
    assert notes[0]["from_label"] == "Gemini"
    assert notes[0]["to_label"] == "Groq"


def test_rate_limit_classified_as_failover() -> None:
    def gemini_429(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("HTTP 429 rate_limit_exceeded: too many tokens per minute")

    def groq_ok(_s, _u, image_bytes=None):  # noqa: ARG001
        return "groq_ok"

    with _stub_callers(gemini=gemini_429, groq=groq_ok):
        assert ai_bridge.call_llm("s", "u") == "groq_ok"
    notes = ai_bridge.drain_provider_notes()
    assert notes and "rate" in notes[0]["reason"].lower()


def test_auth_error_classified_as_failover() -> None:
    def gemini_bad_key(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("API key not valid. Please pass a valid API key.")

    def groq_ok(_s, _u, image_bytes=None):  # noqa: ARG001
        return "groq_ok"

    with _stub_callers(gemini=gemini_bad_key, groq=groq_ok):
        assert ai_bridge.call_llm("s", "u") == "groq_ok"
    notes = ai_bridge.drain_provider_notes()
    assert notes and "authentication" in notes[0]["reason"].lower()


def test_unrelated_error_does_not_failover() -> None:
    """A schema/prompt error has nothing to do with quota; failing over
    would just hide the real bug. Validator catches this kind of issue
    upstream; LLM call itself should re-raise so the caller sees it."""
    class WeirdBug(RuntimeError):
        pass

    def gemini_weird(_s, _u, image_bytes=None):  # noqa: ARG001
        raise WeirdBug("malformed completion: missing 'choices' in response")

    def groq_should_not_run(_s, _u, image_bytes=None):  # noqa: ARG001
        raise AssertionError("groq must not be called for a non-failover error")

    with _stub_callers(gemini=gemini_weird, groq=groq_should_not_run), pytest.raises(WeirdBug):
        ai_bridge.call_llm("s", "u")
    # No switch notes because no successful failover happened.
    assert ai_bridge.drain_provider_notes() == []


def test_all_providers_exhausted_raises_actionable_error() -> None:
    def gemini_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("quota exhausted")

    def groq_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("quota exhausted")

    with _stub_callers(gemini=gemini_quota, groq=groq_quota):
        with pytest.raises(RuntimeError, match="All configured LLM providers"):
            ai_bridge.call_llm("s", "u")


def test_failover_disabled_env_var_keeps_legacy_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators who want fail-fast can opt out via
    ``LLM_FAILOVER_DISABLED=1`` -- the original error is propagated."""
    monkeypatch.setenv("LLM_FAILOVER_DISABLED", "1")

    def gemini_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("quota exhausted")

    def groq_should_not_run(_s, _u, image_bytes=None):  # noqa: ARG001
        raise AssertionError("groq must not be called when failover is disabled")

    with _stub_callers(gemini=gemini_quota, groq=groq_should_not_run):
        with pytest.raises(RuntimeError, match="quota exhausted"):
            ai_bridge.call_llm("s", "u")


def test_explicit_provider_arg_disables_failover() -> None:
    """``provider="gemini"`` forces a single provider; failover off."""
    def gemini_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        raise RuntimeError("quota exhausted")

    def groq_should_not_run(_s, _u, image_bytes=None):  # noqa: ARG001
        raise AssertionError("groq must not be called for forced provider")

    with _stub_callers(gemini=gemini_quota, groq=groq_should_not_run):
        with pytest.raises(RuntimeError, match="quota exhausted"):
            ai_bridge.call_llm("s", "u", provider="gemini")


def test_provider_in_cooldown_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the first failover, a re-call within the cooldown window
    must skip the failed provider entirely (no second 429)."""
    call_log: list[str] = []

    def gemini_quota(_s, _u, image_bytes=None):  # noqa: ARG001
        call_log.append("gemini")
        raise RuntimeError("quota exhausted")

    def groq_ok(_s, _u, image_bytes=None):  # noqa: ARG001
        call_log.append("groq")
        return "groq_ok"

    with _stub_callers(gemini=gemini_quota, groq=groq_ok):
        ai_bridge.call_llm("s", "u")  # First call: gemini fails, groq wins.
        ai_bridge.drain_provider_notes()
        ai_bridge.call_llm("s", "u")  # Second call: gemini in cooldown, skip straight to groq.

    # Gemini should have been called exactly ONCE; groq twice.
    assert call_log.count("gemini") == 1
    assert call_log.count("groq") == 2
