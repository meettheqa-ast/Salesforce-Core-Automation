"""HMAC signature verification for the GitHub webhook.

The integration with FastAPI is exercised at higher levels; here we
pin the cryptographic primitive so a refactor can't silently break
the auth boundary on /webhooks/github.
"""

from __future__ import annotations

import hashlib
import hmac

from ai_qa_portal.backend.services.integrations.github import GitHubProvider


SECRET = "shh-its-a-secret"
BODY = b'{"action":"completed","workflow_run":{"id":1}}'


def _signed(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_signature_valid():
    assert GitHubProvider.verify_webhook_signature(secret=SECRET, body=BODY, header_signature=_signed(BODY))


def test_signature_mismatch_body():
    sig = _signed(BODY)
    tampered = BODY + b"+extra"
    assert not GitHubProvider.verify_webhook_signature(secret=SECRET, body=tampered, header_signature=sig)


def test_signature_mismatch_secret():
    sig = _signed(BODY, secret="other-secret")
    assert not GitHubProvider.verify_webhook_signature(secret=SECRET, body=BODY, header_signature=sig)


def test_signature_missing_header():
    assert not GitHubProvider.verify_webhook_signature(secret=SECRET, body=BODY, header_signature=None)
    assert not GitHubProvider.verify_webhook_signature(secret=SECRET, body=BODY, header_signature="")


def test_signature_missing_secret():
    sig = _signed(BODY)
    assert not GitHubProvider.verify_webhook_signature(secret="", body=BODY, header_signature=sig)


def test_signature_requires_sha256_prefix():
    digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert not GitHubProvider.verify_webhook_signature(secret=SECRET, body=BODY, header_signature=digest)  # missing prefix
