"""Unit tests for ``cursor_sdk_bridge`` and the dispatcher in
``ai_bridge._call_cursor``.

These tests must NOT spawn the real Node sidecar or hit api.cursor.com.
We mock:

* ``shutil.which("node")`` / subprocess-level ``node --version`` for
  ``is_node_available``.
* ``requests.get`` / ``requests.post`` for the HTTP client.
* The bridge module itself when validating the dispatcher in
  ``ai_bridge._call_cursor`` so we never reach the network or spawn
  ``node`` from a unit test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# is_node_available
# ---------------------------------------------------------------------------


def _reset_node_cache() -> None:
    import cursor_sdk_bridge

    cursor_sdk_bridge._node_available_cache = None


def test_is_node_available_false_when_node_missing():
    """No ``node`` on PATH -> sidecar must be considered unavailable.

    This is the most common bare-metal failure mode (devs who haven't
    installed Node yet); the dispatcher must transparently fall through
    to REST.
    """
    import cursor_sdk_bridge

    _reset_node_cache()
    with patch("cursor_sdk_bridge.shutil.which", return_value=None):
        assert cursor_sdk_bridge.is_node_available(force=True) is False


def test_is_node_available_true_when_version_succeeds():
    import cursor_sdk_bridge

    _reset_node_cache()
    fake_proc = SimpleNamespace(returncode=0, stdout="v20.10.0\n", stderr="")
    with patch("cursor_sdk_bridge.shutil.which", return_value="/usr/bin/node"), \
         patch("cursor_sdk_bridge.subprocess.run", return_value=fake_proc):
        assert cursor_sdk_bridge.is_node_available(force=True) is True


def test_is_node_available_caches_result():
    """The result is cached so the failover chain doesn't re-shell out
    on every call. Pass ``force=True`` to invalidate (only used by the
    test suite + manual ops).
    """
    import cursor_sdk_bridge

    _reset_node_cache()
    fake_proc = SimpleNamespace(returncode=0, stdout="v20.10.0\n", stderr="")
    with patch("cursor_sdk_bridge.shutil.which", return_value="/usr/bin/node") as which_mock, \
         patch("cursor_sdk_bridge.subprocess.run", return_value=fake_proc) as run_mock:
        cursor_sdk_bridge.is_node_available(force=True)
        cursor_sdk_bridge.is_node_available()
        cursor_sdk_bridge.is_node_available()
        assert which_mock.call_count == 1
        assert run_mock.call_count == 1


def test_is_node_available_handles_subprocess_timeout():
    import subprocess as _sp

    import cursor_sdk_bridge

    _reset_node_cache()
    with patch("cursor_sdk_bridge.shutil.which", return_value="/usr/bin/node"), \
         patch("cursor_sdk_bridge.subprocess.run", side_effect=_sp.TimeoutExpired("node", 5)):
        assert cursor_sdk_bridge.is_node_available(force=True) is False


# ---------------------------------------------------------------------------
# start_sidecar / stop_sidecar lifecycle
# ---------------------------------------------------------------------------


def test_start_sidecar_is_idempotent(monkeypatch):
    """Calling ``start_sidecar()`` twice must NOT spawn a second Node
    process. The dispatcher hits this path on every LLM call after the
    first; without this guarantee we'd leak processes (and burn the
    8767 port) on every request.
    """
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_API_KEY", "crsr_test")

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # process still alive
    fake_proc.pid = 99999
    cursor_sdk_bridge._sidecar_proc = fake_proc

    try:
        with patch("cursor_sdk_bridge.subprocess.Popen") as popen_mock:
            popen_mock.side_effect = AssertionError(
                "start_sidecar must NOT spawn a second process when one is already running",
            )
            returned = cursor_sdk_bridge.start_sidecar()
            assert returned is fake_proc
            popen_mock.assert_not_called()
    finally:
        cursor_sdk_bridge._sidecar_proc = None


def test_start_sidecar_refuses_without_api_key(monkeypatch):
    """The sidecar can't authenticate without ``CURSOR_API_KEY``; failing
    fast in the bridge avoids a 5s spawn + immediate-exit cycle on every
    LLM call when the key is missing. The dispatcher catches the
    resulting ``CursorSdkError`` and falls through to REST.
    """
    import cursor_sdk_bridge

    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    cursor_sdk_bridge._sidecar_proc = None
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.start_sidecar()
    assert "CURSOR_API_KEY" in str(exc.value)


def test_stop_sidecar_is_safe_when_nothing_running():
    """``atexit.register(stop_sidecar)`` must never raise even if the
    sidecar was never started -- otherwise interpreter shutdown logs a
    spurious traceback every time someone runs the backend without a
    Cursor key.
    """
    import cursor_sdk_bridge

    cursor_sdk_bridge._sidecar_proc = None
    cursor_sdk_bridge.stop_sidecar()  # no exception
    assert cursor_sdk_bridge._sidecar_proc is None


def test_concurrent_start_sidecar_spawns_only_once(monkeypatch):
    """Two threads racing into ``start_sidecar()`` must only Popen once.

    Without the lifecycle lock, both threads would pass the
    ``is_sidecar_running()`` check before either had spawned a Popen,
    and both would race to bind 127.0.0.1:8767 -- one would die with
    EADDRINUSE while the bridge thinks both started successfully.
    """
    import threading

    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_API_KEY", "crsr_test")
    cursor_sdk_bridge._sidecar_proc = None

    popen_calls = 0
    popen_lock = threading.Lock()

    def fake_popen(*_args, **_kwargs):
        nonlocal popen_calls
        with popen_lock:
            popen_calls += 1
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        return proc

    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def runner():
        barrier.wait()
        try:
            cursor_sdk_bridge.start_sidecar()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            errors.append(exc)

    try:
        with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True), \
             patch.object(cursor_sdk_bridge, "_maybe_npm_install"), \
             patch.object(cursor_sdk_bridge, "_kill_orphan_on_port"), \
             patch.object(cursor_sdk_bridge, "_wait_for_sidecar"), \
             patch("cursor_sdk_bridge.subprocess.Popen", side_effect=fake_popen):
            threads = [threading.Thread(target=runner) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
    finally:
        cursor_sdk_bridge._sidecar_proc = None

    assert errors == [], f"start_sidecar raised under concurrency: {errors}"
    assert popen_calls == 1, (
        f"Expected exactly one Popen call under concurrent start; got {popen_calls}"
    )


# ---------------------------------------------------------------------------
# _host_port: BLOCKED_PORTS guard
# ---------------------------------------------------------------------------


def test_host_port_falls_back_when_collision_with_rfmcp(monkeypatch):
    """Using the RF-MCP port for the SDK sidecar would deadlock both;
    the bridge must refuse and fall back to the documented default.
    """
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_SDK_PORT", "8765")  # RF-MCP's port
    host, port = cursor_sdk_bridge._host_port()
    assert host == "127.0.0.1"
    assert port == cursor_sdk_bridge._DEFAULT_PORT  # bumped back to 8767


def test_host_port_uses_explicit_override(monkeypatch):
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_SDK_PORT", "9999")
    host, port = cursor_sdk_bridge._host_port()
    assert (host, port) == ("127.0.0.1", 9999)


# ---------------------------------------------------------------------------
# call_cursor_sdk: success + error mappings
# ---------------------------------------------------------------------------


def _fake_response(status: int, body: dict | None = None, text_body: str | None = None):
    """Build a minimal stand-in for ``requests.Response`` -- only the
    methods the bridge actually invokes.
    """
    resp = MagicMock()
    resp.status_code = status
    resp.text = text_body if text_body is not None else ""
    resp.json = MagicMock(return_value=body if body is not None else {})
    return resp


# NOTE: ``cursor_sdk_bridge`` does ``import requests`` lazily inside
# ``call_cursor_sdk`` and ``is_sidecar_responsive`` (keeps module load
# fast on cold start). Because Python caches modules, patching the
# ``requests`` module itself is equivalent to patching the local
# binding the bridge resolves -- they're the same singleton.


def test_call_cursor_sdk_returns_text_on_200():
    import cursor_sdk_bridge

    fake = _fake_response(200, {"text": "Hello world", "runId": "run_abc"})
    with patch("requests.post", return_value=fake) as post_mock:
        result = cursor_sdk_bridge.call_cursor_sdk(
            system="sys", user="hi", model="composer-2", timeout_s=5,
        )
    assert result == "Hello world"
    post_mock.assert_called_once()
    sent_payload = post_mock.call_args.kwargs["json"]
    assert sent_payload["system"] == "sys"
    assert sent_payload["user"] == "hi"
    assert sent_payload["model"] == "composer-2"


def test_call_cursor_sdk_502_is_retryable_when_marked():
    """``CursorAgentError`` from the SDK -> sidecar returns 502 with
    ``retryable`` reflecting ``error.isRetryable``. The bridge must
    surface that flag so the failover classifier knows whether to
    move on.
    """
    import cursor_sdk_bridge

    fake = _fake_response(502, {"error": "auth failed", "retryable": True})
    with patch("requests.post", return_value=fake):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")
    assert exc.value.retryable is True
    assert exc.value.status_code == 502
    assert "auth failed" in str(exc.value)


def test_call_cursor_sdk_500_run_failure_is_not_retryable():
    """Run-level failure -> 500 + retryable=False. Same prompt won't
    succeed on a retry; the chain should keep moving but NOT retry
    Cursor itself.
    """
    import cursor_sdk_bridge

    fake = _fake_response(500, {"error": "agent error mid-run", "retryable": False})
    with patch("requests.post", return_value=fake):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")
    assert exc.value.retryable is False
    assert exc.value.status_code == 500


def test_call_cursor_sdk_504_timeout_is_retryable():
    """Sidecar's wall-clock timeout -> 504 -> bridge always treats as
    retryable so we can fall over to a faster provider.
    """
    import cursor_sdk_bridge

    fake = _fake_response(504, {"error": "agent timed out", "retryable": True})
    with patch("requests.post", return_value=fake):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")
    assert exc.value.retryable is True


def test_call_cursor_sdk_request_timeout_is_retryable():
    """If the HTTP call itself times out (e.g. sidecar wedged), the
    bridge must classify as retryable so the chain keeps moving.
    """
    import requests

    import cursor_sdk_bridge

    with patch("requests.post", side_effect=requests.exceptions.Timeout("read timeout")):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi", timeout_s=2)
    assert exc.value.retryable is True


def test_call_cursor_sdk_connection_error_is_retryable():
    """Connection refused (sidecar process gone) -> retryable. The
    dispatcher's ``ensure_sidecar`` will have already restarted on the
    next call, so a downstream retry is reasonable.
    """
    import requests

    import cursor_sdk_bridge

    with patch(
        "requests.post",
        side_effect=requests.exceptions.ConnectionError("refused"),
    ), pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
        cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")
    assert exc.value.retryable is True


def test_call_cursor_sdk_non_json_response_raises():
    """If the sidecar returns garbage HTML / partial body, surface it
    cleanly (don't raise a JSONDecodeError that the failover classifier
    won't recognise).
    """
    import cursor_sdk_bridge

    fake = _fake_response(200, body=None, text_body="<html>oops</html>")
    fake.json.side_effect = ValueError("not json")
    with patch("requests.post", return_value=fake):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc:
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")
    assert exc.value.retryable is False
    assert "non-JSON" in str(exc.value)


def test_call_cursor_sdk_empty_text_raises():
    """200 but empty ``text`` field is still a contract violation; we
    don't want to silently propagate empty completions to the validator
    loop.
    """
    import cursor_sdk_bridge

    fake = _fake_response(200, {"text": ""})
    with patch("requests.post", return_value=fake):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError):
            cursor_sdk_bridge.call_cursor_sdk(system="", user="hi")


# ---------------------------------------------------------------------------
# is_sidecar_responsive: HTTP probe
# ---------------------------------------------------------------------------


def test_is_sidecar_responsive_true_on_200():
    import cursor_sdk_bridge

    fake = _fake_response(200, {"ok": True})
    with patch("requests.get", return_value=fake):
        assert cursor_sdk_bridge.is_sidecar_responsive(timeout=0.1) is True


def test_is_sidecar_responsive_false_on_failure():
    import cursor_sdk_bridge

    with patch("requests.get", side_effect=OSError("connect refused")):
        assert cursor_sdk_bridge.is_sidecar_responsive(timeout=0.1) is False


# ---------------------------------------------------------------------------
# ai_bridge._call_cursor dispatcher
# ---------------------------------------------------------------------------
# These tests verify the dispatcher contract -- the part of the change
# that determines whether we hit the SDK sidecar or the legacy REST
# path. Critical for the no-regression guarantee.


def test_dispatcher_uses_rest_when_use_sdk_false(monkeypatch):
    """``CURSOR_USE_SDK=false`` -> dispatcher must NOT touch the SDK
    bridge and must call ``_call_cursor_rest`` verbatim.
    """
    import ai_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "false")
    with patch.object(ai_bridge, "_call_cursor_rest", return_value="REST_OK") as rest_mock:
        out = ai_bridge._call_cursor("sys", "user")
    assert out == "REST_OK"
    rest_mock.assert_called_once_with("sys", "user", None)


def test_dispatcher_uses_rest_when_node_unavailable(monkeypatch):
    """No Node on PATH -> SDK detection returns False -> dispatcher
    falls through to REST silently. This is the most important
    no-regression branch.
    """
    import ai_bridge
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "true")
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=False), \
         patch.object(ai_bridge, "_call_cursor_rest", return_value="REST_OK") as rest_mock:
        out = ai_bridge._call_cursor("sys", "user")
    assert out == "REST_OK"
    rest_mock.assert_called_once()


def test_dispatcher_calls_sdk_when_available(monkeypatch):
    """SDK happy path: Node + sidecar healthy -> dispatcher calls the
    bridge and returns its text without invoking REST.
    """
    import ai_bridge
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "true")
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_test")
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True), \
         patch.object(cursor_sdk_bridge, "ensure_sidecar"), \
         patch.object(cursor_sdk_bridge, "call_cursor_sdk", return_value="SDK_OK"), \
         patch.object(ai_bridge, "_call_cursor_rest") as rest_mock:
        out = ai_bridge._call_cursor("sys", "user")
    assert out == "SDK_OK"
    rest_mock.assert_not_called()


def test_dispatcher_falls_back_when_sidecar_start_fails(monkeypatch):
    """``ensure_sidecar`` raising a non-CursorSdkError (e.g. a generic
    OSError from spawn) -> dispatcher logs and falls through to REST.
    Genuine bugs should not break end-user generation.
    """
    import ai_bridge
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "true")
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True), \
         patch.object(cursor_sdk_bridge, "ensure_sidecar", side_effect=OSError("spawn EACCES")), \
         patch.object(ai_bridge, "_call_cursor_rest", return_value="REST_OK") as rest_mock:
        out = ai_bridge._call_cursor("sys", "user")
    assert out == "REST_OK"
    rest_mock.assert_called_once()


def test_dispatcher_propagates_cursor_sdk_error(monkeypatch):
    """A real ``CursorSdkError`` (e.g. quota exceeded) must surface to
    the failover chain rather than silently retrying via REST. Otherwise
    a 429 against Cursor would double-bill the user (REST + SDK) before
    the chain ever moves to Gemini.

    The exception type is preserved (``CursorSdkError`` extends
    ``RuntimeError``) so callers that want the structured ``retryable``
    /``status_code`` fields can ``isinstance()``-check; the failover
    classifier in ``call_llm`` reads the formatted message text and
    routes on quota/auth/rate-limit keywords as usual.
    """
    import ai_bridge
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "true")
    err = cursor_sdk_bridge.CursorSdkError("quota exhausted", retryable=False, status_code=429)
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True), \
         patch.object(cursor_sdk_bridge, "ensure_sidecar"), \
         patch.object(cursor_sdk_bridge, "call_cursor_sdk", side_effect=err), \
         patch.object(ai_bridge, "_call_cursor_rest") as rest_mock:
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc_info:
            ai_bridge._call_cursor("sys", "user")
    rest_mock.assert_not_called()
    assert "quota exhausted" in str(exc_info.value)
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 429


def test_dispatcher_preserves_retryable_flag(monkeypatch):
    """The dispatcher must NOT swallow or rewrite the ``retryable``
    flag on a propagated ``CursorSdkError`` -- the field is the only
    structured signal a caller has for "should we retry Cursor itself
    before falling over to Gemini?".
    """
    import ai_bridge
    import cursor_sdk_bridge

    monkeypatch.setenv("CURSOR_USE_SDK", "true")
    err = cursor_sdk_bridge.CursorSdkError("temporary outage", retryable=True)
    with patch.object(cursor_sdk_bridge, "is_node_available", return_value=True), \
         patch.object(cursor_sdk_bridge, "ensure_sidecar"), \
         patch.object(cursor_sdk_bridge, "call_cursor_sdk", side_effect=err):
        with pytest.raises(cursor_sdk_bridge.CursorSdkError) as exc_info:
            ai_bridge._call_cursor("sys", "user")
    assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# _default_primary_provider
# ---------------------------------------------------------------------------


def test_default_primary_is_cursor_when_key_set(monkeypatch):
    """Setting ``CURSOR_API_KEY`` is the only step a user takes to
    promote Cursor to primary -- this guards that contract.
    """
    import ai_bridge

    monkeypatch.setenv("CURSOR_API_KEY", "crsr_test")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert ai_bridge._default_primary_provider() == "cursor"


def test_default_primary_falls_back_to_ollama_or_gemini_without_key(monkeypatch):
    """No key -> behaviour identical to the pre-change branch
    (ollama-if-reachable, else gemini). This is the no-regression
    anchor for users who've never heard of Cursor.
    """
    import ai_bridge

    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with patch.object(ai_bridge, "_ollama_is_reachable", return_value=False):
        assert ai_bridge._default_primary_provider() == "gemini"
    with patch.object(ai_bridge, "_ollama_is_reachable", return_value=True):
        assert ai_bridge._default_primary_provider() == "ollama"
