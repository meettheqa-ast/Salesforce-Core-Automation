"""GitHub provider used for script storage + CI execution.

Two authentication paths:

* **GitHub App** (preferred): the portal mints a short-lived installation
  token from the app's RSA private key + installation id. Tokens are
  cached for ~50 minutes (GitHub's hard expiry is 60 minutes).
* **Personal Access Token (PAT)**: the portal stores a Fernet-encrypted
  PAT and uses it directly as the bearer credential. Useful for projects
  that haven't installed an org-wide App.

The provider exposes the methods the rest of the portal needs:

* ``test_connection``      -- cheap probe (``GET /user``).
* ``list_repos``           -- repos visible to this installation / PAT.
* ``push_files``           -- upsert one or more files into a branch via
  the Contents API. Used for script storage.
* ``read_file``            -- pull-back of any file in the repo.
* ``ensure_workflow_yaml`` -- writes/updates the portal-owned workflow.
* ``trigger_workflow_dispatch`` -- kicks a run.
* ``get_workflow_run``     -- poll fallback when webhooks aren't reachable.
* ``verify_webhook_signature`` -- HMAC-SHA256 per
  https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries.

All network calls go through ``httpx.Client`` so the provider is
synchronous and thread-safe for FastAPI's threadpool dispatch.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx
import jwt

logger = logging.getLogger("ai_qa_portal.integrations.github")

_GITHUB_API = "https://api.github.com"
_USER_AGENT = "AI-QA-Portal"
_INSTALLATION_TOKEN_CACHE_S = 50 * 60  # GitHub installation tokens live 60 min


class GitHubAuthError(RuntimeError):
    """4xx auth failure."""


class GitHubRateLimitError(RuntimeError):
    """Repeated 429/403-rate-limit exhausted retries."""


@dataclass
class _CachedToken:
    token: str = ""
    expires_at: float = 0.0


@dataclass
class GitHubProvider:
    """Sync client. Either ``app_id`` + ``installation_id`` + ``private_key_pem``
    OR ``access_token`` must be supplied. Mixing both prefers the App path."""

    app_id: str = ""
    installation_id: str = ""
    private_key_pem: str = ""
    access_token: str = ""
    owner_login: str = ""
    timeout_s: float = 30.0
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    _cached: _CachedToken = field(default_factory=_CachedToken, init=False, repr=False)

    def __post_init__(self) -> None:
        if not (self.app_id and self.installation_id and self.private_key_pem) and not self.access_token:
            raise ValueError(
                "GitHubProvider requires either (app_id + installation_id + private_key_pem) or access_token"
            )
        self._client = httpx.Client(
            base_url=_GITHUB_API,
            timeout=self.timeout_s,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": _USER_AGENT,
            },
        )

    # ---- lifecycle ----------------------------------------------------

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "GitHubProvider":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    # ---- auth ---------------------------------------------------------

    def _is_app(self) -> bool:
        return bool(self.app_id and self.installation_id and self.private_key_pem)

    def _app_jwt(self) -> str:
        """RS256-signed JWT identifying the GitHub App itself (10-min max)."""
        now = int(time.time())
        payload = {"iat": now - 30, "exp": now + 9 * 60, "iss": str(self.app_id)}
        return jwt.encode(payload, self.private_key_pem, algorithm="RS256")

    def _installation_token(self) -> str:
        """Mint or reuse a cached installation token."""
        if self._cached.token and self._cached.expires_at - 60 > time.time():
            return self._cached.token
        assert self._client is not None
        jwt_token = self._app_jwt()
        resp = self._client.post(
            f"/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        if resp.status_code != 201:
            raise GitHubAuthError(
                f"GitHub App token mint failed {resp.status_code}: {resp.text[:300]}"
            )
        token = resp.json().get("token", "")
        self._cached = _CachedToken(token=token, expires_at=time.time() + _INSTALLATION_TOKEN_CACHE_S)
        return token

    def _bearer(self) -> str:
        if self._is_app():
            return self._installation_token()
        return self.access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer()}"}

    # ---- low-level ----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None
        retries = 0
        while True:
            resp = self._client.request(method, path, headers=self._headers(), **kwargs)
            if resp.status_code in (401, 403) and "rate limit" not in resp.text.lower():
                raise GitHubAuthError(f"GitHub {resp.status_code} on {method} {path}: {resp.text[:300]}")
            if resp.status_code in (403, 429) and "rate limit" in resp.text.lower():
                if retries >= 4:
                    raise GitHubRateLimitError(f"GitHub rate-limited after retries: {resp.text[:200]}")
                retry_after = float(resp.headers.get("Retry-After", 2 ** retries))
                logger.warning("GitHub rate-limited; sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                retries += 1
                continue
            return resp

    # ---- connection ---------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        # For App auth, /user isn't meaningful (the App is not a user). Use
        # /installation/repositories to confirm the App can see anything.
        path = "/installation/repositories" if self._is_app() else "/user"
        resp = self._request("GET", path)
        if resp.status_code != 200:
            raise GitHubAuthError(f"GitHub test failed {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if self._is_app():
            return {"installation_id": self.installation_id, "repo_count": body.get("total_count")}
        return body

    # ---- repos --------------------------------------------------------

    def list_repos(self) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            path = (
                "/installation/repositories"
                if self._is_app()
                else "/user/repos"
            )
            resp = self._request(
                "GET", path,
                params={"per_page": 100, "page": page, "affiliation": "owner,collaborator,organization_member"},
            )
            if resp.status_code != 200:
                raise GitHubAuthError(f"GitHub list repos {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            chunk = payload.get("repositories", payload) if isinstance(payload, dict) else payload
            if not chunk:
                break
            for r in chunk:
                repos.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "owner": (r.get("owner") or {}).get("login"),
                    "full_name": r.get("full_name"),
                    "default_branch": r.get("default_branch") or "main",
                    "private": r.get("private"),
                    "html_url": r.get("html_url"),
                })
            if len(chunk) < 100:
                break
            page += 1
        return repos

    # ---- contents -----------------------------------------------------

    def read_file(self, *, owner: str, repo: str, path: str, ref: str | None = None) -> tuple[str, str] | None:
        """Return (content, sha) for ``path``, or ``None`` if it doesn't
        exist."""
        params = {"ref": ref} if ref else {}
        resp = self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubAuthError(f"GitHub read file {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        content_b64 = body.get("content", "")
        try:
            content = base64.b64decode(content_b64.encode()).decode("utf-8", errors="replace")
        except Exception:
            content = ""
        return content, body.get("sha", "")

    def push_files(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        files: Iterable[dict[str, str]],
        commit_message: str,
    ) -> dict[str, Any]:
        """Upsert each ``{path, content}`` entry on ``branch``. Uses the
        Contents API one PUT per file (sufficient for tens of files; for
        a true atomic multi-file commit we'd use the Git Data API tree
        builder, which is a future optimisation)."""
        results: list[dict[str, Any]] = []
        for f in files:
            path = f["path"]
            content = f["content"]
            existing = self.read_file(owner=owner, repo=repo, path=path, ref=branch)
            body: dict[str, Any] = {
                "message": commit_message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if existing is not None:
                body["sha"] = existing[1]
            resp = self._request(
                "PUT",
                f"/repos/{owner}/{repo}/contents/{path}",
                json=body,
            )
            if resp.status_code not in (200, 201):
                raise GitHubAuthError(
                    f"GitHub push {path} -> {resp.status_code}: {resp.text[:200]}"
                )
            results.append(resp.json())
        return {"committed": len(results), "files": [r.get("content", {}).get("path") for r in results]}

    # ---- workflows ----------------------------------------------------

    def ensure_workflow_yaml(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        workflow_path: str,
        yaml_content: str,
        commit_message: str = "chore: portal sync of automation workflow",
    ) -> dict[str, Any]:
        """Write or update the portal-owned workflow YAML."""
        return self.push_files(
            owner=owner,
            repo=repo,
            branch=branch,
            files=[{"path": workflow_path, "content": yaml_content}],
            commit_message=commit_message,
        )

    def trigger_workflow_dispatch(
        self,
        *,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: dict[str, str] | None = None,
    ) -> None:
        body: dict[str, Any] = {"ref": ref}
        if inputs:
            body["inputs"] = inputs
        resp = self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json=body,
        )
        if resp.status_code not in (204, 201):
            raise GitHubAuthError(
                f"workflow_dispatch failed {resp.status_code}: {resp.text[:200]}"
            )

    def list_workflow_runs(self, *, owner: str, repo: str, workflow_id: str, per_page: int = 20) -> list[dict[str, Any]]:
        resp = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs",
            params={"per_page": per_page},
        )
        if resp.status_code != 200:
            raise GitHubAuthError(f"list runs {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("workflow_runs", [])

    def get_workflow_run(self, *, owner: str, repo: str, run_id: str | int) -> dict[str, Any] | None:
        resp = self._request("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubAuthError(f"get run {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ---- webhooks -----------------------------------------------------

    @staticmethod
    def verify_webhook_signature(*, secret: str, body: bytes, header_signature: str | None) -> bool:
        """Return True iff ``header_signature`` matches HMAC-SHA256 of
        ``body`` using ``secret``. We do constant-time comparison; the
        method is a staticmethod so the webhook handler can call it
        without instantiating a full provider."""
        if not secret or not header_signature:
            return False
        if not header_signature.startswith("sha256="):
            return False
        sent = header_signature.removeprefix("sha256=").strip()
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sent, digest)
