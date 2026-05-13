"""Google ID token verification + the FastAPI ``get_current_user`` dependency.

Auth model:
  Frontend (Vercel + NextAuth) handles the Google OAuth flow and stores the
  Google-issued ``id_token`` in the session. ``/api/auth/jwt`` on the frontend
  hands that raw ID token to the browser, which forwards it to this backend as
  ``Authorization: Bearer <token>`` (or as ``?token=<token>`` for SSE / <a href>
  / <img src> URLs that cannot set a header).

  This backend verifies the ID token directly against Google's JWKS at
  https://www.googleapis.com/oauth2/v3/certs -- no shared secret with the
  frontend, no encrypted JWE handling, no NextAuth internals to track. The
  trust anchor is Google's signing keys + our OAuth Client ID as the expected
  audience.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings

from .db import (
    GlobalRole,
    ProjectRole,
    User,
    get_db,
    get_membership,
    get_or_create_user,
    project_role_at_least,
)

logger = logging.getLogger("ai_qa_portal.auth")


# Google's published JWKS endpoint (rotated keys; PyJWKClient caches internally).
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"]

# Lazy singleton -- PyJWKClient does its own key caching with a default TTL.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_GOOGLE_JWKS_URL, cache_keys=True)
    return _jwks_client


def verify_google_id_token(token: str) -> dict[str, Any]:
    """Verify a Google-issued ID token via JWKS. Raises 401 on any failure."""
    client_id = settings.google_client_id.strip()
    if not client_id:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Backend auth not configured: GOOGLE_CLIENT_ID is empty",
        )

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=_GOOGLE_ISSUERS,
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google ID token expired") from None
    except jwt.InvalidAudienceError:
        # Almost always indicates the frontend's GOOGLE_CLIENT_ID and the backend's
        # GOOGLE_CLIENT_ID came from different OAuth clients. Flagged loudly because
        # this would otherwise be confusing in production.
        logger.warning(
            "Google ID token audience mismatch -- frontend GOOGLE_CLIENT_ID and "
            "backend GOOGLE_CLIENT_ID likely point at different OAuth clients"
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token audience mismatch -- frontend/backend Google client IDs differ",
        ) from None
    except jwt.InvalidIssuerError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token issuer") from None
    except jwt.InvalidTokenError as exc:
        logger.info("Google ID token verification failed: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google ID token") from None
    except Exception as exc:  # JWKS network failure, etc.
        logger.exception("JWKS verification failed unexpectedly")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not verify token (JWKS error): {exc}",
        ) from None


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authorization must be 'Bearer <token>'")
    return parts[1].strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    token_q: str | None = Query(default=None, alias="token"),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the current logged-in user from a Google ID token.

    Token source: ``Authorization: Bearer <token>`` header preferred; falls back
    to ``?token=<jwt>`` query param for SSE / <a href> / <img src> calls that
    can't attach a header.

    Behaviour with ``settings.auth_disabled``: returns a synthetic ``dev@local``
    admin so the unauthenticated demo build keeps working.
    """
    if settings.auth_disabled:
        user = get_or_create_user(
            db,
            email="dev@local",
            name="Dev (auth disabled)",
            picture="",
        )
        if not user.is_admin:
            user.is_admin = True
            db.commit()
            db.refresh(user)
        return user

    if authorization:
        token = _extract_bearer(authorization)
    elif token_q:
        token = token_q.strip()
    else:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header or ?token= query param",
        )

    payload = verify_google_id_token(token)

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token is missing 'email' claim")

    domain = settings.allowed_email_domain.strip().lower()
    if domain:
        # Google sets `hd` (hosted domain) on Workspace ID tokens. Prefer that
        # claim when present; fall back to email-suffix matching for personal
        # accounts that happen to share the domain (rare but defensible).
        hd = (payload.get("hd") or "").strip().lower()
        if hd:
            if hd != domain:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Only @{domain} accounts may access this portal",
                )
        elif not email.endswith("@" + domain):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Only @{domain} accounts may access this portal",
            )

    user = get_or_create_user(
        db,
        email=email,
        name=str(payload.get("name") or ""),
        picture=str(payload.get("picture") or ""),
    )

    # Phase 2a: respect deactivation + force-revoked sessions.
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")
    if user.session_revoked_at is not None:
        iat = payload.get("iat")
        if isinstance(iat, (int, float)):
            iat_dt = datetime.fromtimestamp(int(iat), tz=UTC)
            if iat_dt < user.session_revoked_at:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Session was revoked; please sign in again",
                )

    # Best-effort last_login_at refresh; don't fail the request if it errors.
    try:
        user.last_login_at = datetime.now(UTC)
        db.commit()
    except Exception:
        db.rollback()
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that 403s for non-admin users. Used by admin-only endpoints."""
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return current_user


# --- Project ownership helper (used by /personas, /orgs, /user-stories) ---

def user_owns_project_uuid(current_user: User, project_id: UUID | str) -> bool:
    """True if *current_user* owns the project this UUID maps to.

    Admin always true. Resolves the project_id back to its filesystem slug via
    ``project_registry.slug_for_project_id``; returns False if the slug is
    unknown (cannot establish ownership) so create endpoints fail closed.
    """
    if current_user.is_admin:
        return True
    # Local import to dodge the project_registry -> services circular path.
    import project_manager
    from ai_qa_portal.backend.project_registry import slug_for_project_id

    slug = slug_for_project_id(project_id)
    if not slug:
        return False
    owner = project_manager.get_project_owner(slug)
    return bool(owner) and owner == current_user.id


def assert_user_owns_project(current_user: User, project_id: UUID | str) -> None:
    """Raise 403 unless *current_user* owns the project this UUID maps to."""
    if not user_owns_project_uuid(current_user, project_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You do not have access to this project",
        )


def user_can_create_in_project(
    db: Session,
    current_user: User,
    project_id: UUID | str,
    min_role: ProjectRole | None = None,
) -> bool:
    """True if *current_user* may create resources (sprints, stories, etc.) in
    the project this UUID maps to.

    Resolution order:
      1. Admin -> True. Admins can create anywhere.
      2. Filesystem owner of the project's slug -> True. This is the
         legacy fallback: projects created before the DB-membership table
         existed only carry an owner on disk; we keep this path so those
         projects don't 403 on every action.
      3. DB membership at >= ``min_role`` on the project's slug -> True.
      4. Otherwise False.

    The default minimum role is **lead** (the project-level "team lead"
    role; ``ProjectRole.lead``) -- the same bar as
    ``POST /api/projects/{name}/members`` and the invitation-create endpoint.
    Keeping the threshold consistent across creation-style endpoints means a
    user who can invite teammates to a project can also scaffold sprints
    and stories in it; viewers and plain members cannot.
    """
    # Late binding so callers can pass ``None`` to get the default without
    # having to import ProjectRole themselves.
    if min_role is None:
        min_role = ProjectRole.lead

    if current_user.is_admin:
        return True
    # Local imports to dodge the project_registry -> services circular path
    # and to keep ``project_manager`` (a CLI-era top-level module) lazy.
    import project_manager
    from ai_qa_portal.backend.project_registry import slug_for_project_id

    slug = slug_for_project_id(project_id)
    if not slug:
        return False
    # Filesystem-owner fallback (legacy projects without DB memberships).
    owner = project_manager.get_project_owner(slug)
    if owner and owner == current_user.id:
        return True
    # DB membership at >= min_role.
    actual = effective_project_role(db, current_user, slug)
    if actual is None:
        return False
    return project_role_at_least(actual, min_role)


def assert_user_can_create_in_project(
    db: Session,
    current_user: User,
    project_id: UUID | str,
    min_role: ProjectRole | None = None,
) -> None:
    """Raise 403 unless *current_user* may create resources in the project.

    See :func:`user_can_create_in_project` for the resolution order.
    """
    if min_role is None:
        min_role = ProjectRole.lead
    if not user_can_create_in_project(db, current_user, project_id, min_role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This action requires at least the '{min_role.value}' role on the project",
        )


# --- Phase 2a: effective role resolver ------------------------------------

def effective_project_role(
    db: Session,
    user: User,
    project_slug: str,
) -> ProjectRole | None:
    """Return the user's effective role on this project, or None if no access.

    Resolution order (per the locked architecture decision):
      1. Global Admin -> implicit PM on every project (read+write+delete).
      2. Otherwise, the per-project membership role if one exists.
      3. Otherwise, None (caller treats as no access).

    The project_slug is the filesystem slug (matching Saved_Projects/<slug>/).
    """
    if user.global_role == GlobalRole.admin.value or user.is_admin:
        return ProjectRole.pm
    membership = get_membership(db, project_slug=project_slug, user_id=user.id)
    if membership is None:
        return None
    return ProjectRole(membership.role)


def assert_project_role_at_least(
    db: Session,
    user: User,
    project_slug: str,
    required: ProjectRole,
) -> None:
    """Raise 403/404 unless the user has *at least* the required role on the project."""
    actual = effective_project_role(db, user, project_slug)
    if actual is None:
        # 404 over 403 to avoid leaking project existence to non-members.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if not project_role_at_least(actual, required):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This action requires at least the '{required.value}' role on the project",
        )


# Backwards-compat alias used by routers written before Phase 2a; still works
# but routers should migrate to assert_project_role_at_least over time.
def assert_user_can_view_project(
    db: Session, user: User, project_slug: str,
) -> None:
    assert_project_role_at_least(db, user, project_slug, ProjectRole.member)


def require_global_role(*allowed: GlobalRole | str):
    """Build a FastAPI dependency that 403s unless the user's global_role is in `allowed`.

    Admin is always implicitly allowed. Use as:
        @router.post("/foo", dependencies=[Depends(require_global_role(GlobalRole.project_manager))])
    """
    allowed_set = {a.value if isinstance(a, GlobalRole) else a for a in allowed}
    allowed_set.add(GlobalRole.admin.value)

    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.global_role not in allowed_set and not current_user.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of these global roles: {sorted(allowed_set)}",
            )
        return current_user

    return _dep
