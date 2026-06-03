"""Canonical project-access helpers (Phase 3 IA audit -- foundation).

The audit flagged three parallel authorization models in the backend:

  * Membership-based RBAC -- ``assert_project_role_at_least``
    (project_memberships table).
  * Filesystem-owner -- ``assert_user_owns_project``
    (config.json on disk).
  * Per-entity ``owner_user_id`` -- inline checks in story / sprint /
    test-case routers.

This module is the **single canonical entry point** new code should
use. Internally it consults all three layers in the right order so we
don't break the data we already have on disk (filesystem-owner) or in
the user-stories JSON store (per-entity owner). The legacy helpers
(``auth.assert_user_owns_project`` etc.) stay in place and continue
to work; this module just gives us one well-named call to migrate
toward.

Migration policy:

  1. **NOW (this PR)**: ship this module + the canonical helpers.
     New code uses ``require_project_access`` exclusively.
  2. **Next release**: migrate the highest-traffic routers (projects,
     test_cases, sprints, user_stories) to use these helpers. Audit
     calls of the legacy helpers each time you touch a file.
  3. **Release after that**: every router uses these helpers. The
     legacy helpers in ``auth.py`` become thin wrappers that
     call into here, then get removed when no callers remain.
  4. **Future**: backfill `project_memberships` rows for every
     project that today only has a filesystem owner. Once the
     membership table is the only source of truth, drop the
     filesystem-owner fallback inside ``require_project_access``.

This file is intentionally small. The complexity is in the
underlying helpers; we just compose them into one place.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .auth import (
    assert_project_role_at_least,
    user_owns_project_uuid,
)
from .db import ProjectRole, User

logger = logging.getLogger("ai_qa_portal.access")


def require_project_access(
    db: Session,
    user: User,
    project_identifier: str,
    *,
    role: ProjectRole = ProjectRole.member,
) -> None:
    """Raise 403 unless the user has at least ``role`` on the project.

    The canonical project access check. Composes:

      1. ``user.is_admin`` -- admins pass everything (defensive
         re-check; the underlying helpers honour this too).
      2. ``assert_project_role_at_least`` -- the modern
         membership-based RBAC. This is the path that should pass
         in the long run.
      3. ``user_owns_project_uuid`` -- legacy filesystem-owner
         fallback. Kept active until every project has a real
         membership row (Phase 3 step 4 above).

    ``project_identifier`` accepts either the project slug (the
    filesystem name) OR a project UUID -- the underlying helpers
    normalise.

    Raises ``HTTPException(403)`` on denial so the router can let it
    bubble up to FastAPI's exception handler.
    """
    if user.is_admin:
        return
    # Modern path -- membership RBAC.
    try:
        assert_project_role_at_least(db, user, project_identifier, role)
        return
    except HTTPException:
        # Fall through to the legacy filesystem-owner check.
        pass
    # Legacy path -- filesystem-owner fallback. Only meaningful when
    # the identifier resolves to a UUID; ``user_owns_project_uuid``
    # silently returns False for non-UUID strings, so we narrow first.
    try:
        if user_owns_project_uuid(user, project_identifier):
            logger.debug(
                "access: legacy filesystem-owner fallback granted "
                "user=%s project=%s", user.id, project_identifier,
            )
            return
    except Exception:
        pass
    raise HTTPException(
        403,
        f"User {user.id} lacks {role.value} access to project {project_identifier}",
    )


def can_create_in_project(
    db: Session,
    user: User,
    project_identifier: str,
) -> bool:
    """Boolean variant of ``require_project_access`` at the "lead"
    role -- the level required to create stories / sprints / TCs
    inside a project. Returns True/False instead of raising so
    callers can branch (e.g. conditionally render a Create button)."""
    try:
        require_project_access(db, user, project_identifier, role=ProjectRole.lead)
    except HTTPException:
        return False
    return True
