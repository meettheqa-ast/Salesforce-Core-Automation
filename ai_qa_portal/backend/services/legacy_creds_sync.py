# Defensive: this module never wants a stray IO/decode/decrypt error to break
# a list endpoint -- callers ignore the return value and the synced data is
# best-effort. Hence the broad excepts below.
# pylint: disable=broad-exception-caught
"""Bridge the legacy `Saved_Projects/<slug>/config.json` credential store to
the portal-side `SalesforceOrg` + `Persona` JSON store.

Why this exists: the app has two parallel credential systems. The legacy one
predates the bulk-run panel and stores creds per-environment+persona under
the filesystem project. The portal one is what `/orgs` and `/personas` read.
Without this sync, a user who configured Salesforce credentials the
"normal" way (Workspace bar at the top of Generate / project detail page)
would see an empty Org dropdown in the bulk-execution panel.

This module is intentionally one-way (legacy -> portal). The portal side is
always allowed to edit/rotate credentials; we never write back into
`config.json` from here. We also never overwrite portal records that already
exist -- only fill in missing ones.

Performance: the backfill scans `config.json` for each project on every list
call. That's fine for the dozens-of-projects scale we run at; revisit if it
ever becomes hot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import project_manager

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
from ..models.org import OrgType, SalesforceOrg
from ..models.persona import Persona, PersonaVisibility
from ..services.credential_service import CredentialService
from ..services.db import User
from ..storage.json_file_backend import JsonFileBackend

logger = logging.getLogger("ai_qa_portal.legacy_creds_sync")

_store = JsonFileBackend(settings.data_dir)
_ORGS_KEY = "orgs"
_PERSONAS_KEY = "personas"


def _load_orgs() -> list[dict]:
    return _store.read(_ORGS_KEY).get("items", [])


def _save_orgs(items: list[dict]) -> None:
    _store.write(_ORGS_KEY, {"items": items})


def _load_personas() -> list[dict]:
    return _store.read(_PERSONAS_KEY).get("items", [])


def _save_personas(items: list[dict]) -> None:
    _store.write(_PERSONAS_KEY, {"items": items})


def _find_org(orgs: list[dict], project_id: UUID, env_name: str) -> Optional[dict]:
    for o in orgs:
        if str(o.get("project_id")) == str(project_id) and o.get("name") == env_name:
            return o
    return None


def _find_persona(
    personas: list[dict],
    project_id: UUID,
    org_id: UUID,
    persona_name: str,
) -> Optional[dict]:
    pid_s = str(project_id)
    oid_s = str(org_id)
    for p in personas:
        if (
            str(p.get("project_id")) == pid_s
            and str(p.get("org_id")) == oid_s
            and p.get("name") == persona_name
        ):
            return p
    return None


def _valid_org_type(env_name: str) -> Optional[OrgType]:
    """Map an environment name from config.json to the OrgType enum.
    Custom env names (anything not Dev/QA/UAT/Prod) are skipped -- the portal
    Org model doesn't accept them today."""
    try:
        return OrgType(env_name)
    except ValueError:
        return None


def sync_project_from_config(
    project_id: UUID,
    current_user: User,
    *,
    slug: Optional[str] = None,
) -> dict:
    """Idempotent sync. Returns a small summary so callers can log if they
    want.

    Behaviour:
      * For each environment in `Saved_Projects/<slug>/config.json` whose
        name is a valid `OrgType`, ensure a portal `SalesforceOrg` exists.
      * For each persona within that environment that has a non-empty
        username, ensure a portal `Persona` exists with the password
        re-encrypted via `CredentialService` (Fernet).
      * Existing rows are never modified -- this means rotating a password
        through the portal won't be clobbered by a config.json that still
        holds the old one.

    The current user owns any newly-created records, matching how the
    legacy slug-scoped access works (only project owners reach this path).
    """
    project_slug = slug or slug_for_project_id(project_id)
    if not project_slug:
        return {"synced": False, "reason": "no slug for project"}

    try:
        env_names = project_manager.list_environments(project_slug)
    except Exception as exc:
        logger.warning("legacy_creds_sync: list_environments(%s) failed: %s", project_slug, exc)
        return {"synced": False, "reason": str(exc)}

    if not env_names:
        return {"synced": True, "orgs_added": 0, "personas_added": 0}

    cred_svc = CredentialService(settings.fernet_key or None)
    orgs = _load_orgs()
    personas = _load_personas()
    orgs_added = 0
    personas_added = 0
    orgs_changed = False
    personas_changed = False
    now_iso = datetime.now(timezone.utc).isoformat()

    for env_name in env_names:
        org_type = _valid_org_type(env_name)
        if org_type is None:
            continue  # custom env we can't represent yet

        try:
            persona_names = project_manager.list_personas(project_slug, env_name)
        except Exception as exc:  
            logger.warning(
                "legacy_creds_sync: list_personas(%s,%s) failed: %s",
                project_slug, env_name, exc,
            )
            continue
        if not persona_names:
            continue

        # Pick a login URL: first persona with a non-empty sandbox_url wins.
        # Personas in the same env almost always share the same URL; this
        # also gives us the URL needed to seed the SalesforceOrg row.
        login_url = ""
        for pname in persona_names:
            try:
                cfg = project_manager.read_project_config(project_slug, env_name, pname)
            except Exception:
                continue
            url = (cfg.get("sandbox_url") or "").strip()
            if url:
                login_url = url
                break
        if not login_url:
            # No URL anywhere in this env -- skip the env entirely; an Org
            # with empty login_url is useless for runs.
            continue

        existing_org = _find_org(orgs, project_id, env_name)
        if existing_org is None:
            new_org = SalesforceOrg(
                id=uuid4(),
                project_id=project_id,
                name=env_name,
                login_url=login_url,
                org_type=org_type,
                owner_user_id=current_user.id or "",
            )
            org_dict = new_org.model_dump(mode="json")
            orgs.append(org_dict)
            existing_org = org_dict
            orgs_added += 1
            orgs_changed = True

        org_id = UUID(str(existing_org["id"]))

        for pname in persona_names:
            try:
                cfg = project_manager.read_project_config(project_slug, env_name, pname)
            except Exception as exc:  
                logger.warning(
                    "legacy_creds_sync: read_project_config(%s,%s,%s) failed: %s",
                    project_slug, env_name, pname, exc,
                )
                continue

            username = (cfg.get("username") or "").strip()
            password_plain = cfg.get("password") or ""
            if not username or not password_plain:
                continue  # nothing to import -- skip silently

            existing_persona = _find_persona(personas, project_id, org_id, pname)
            if existing_persona is not None:
                continue  # never overwrite a portal-managed persona

            try:
                encrypted = cred_svc.encrypt(password_plain)
            except Exception as exc:  
                logger.warning(
                    "legacy_creds_sync: encrypt failed for %s/%s/%s: %s",
                    project_slug, env_name, pname, exc,
                )
                continue

            default_app_raw = (cfg.get("default_app") or "").strip()
            new_persona = Persona(
                id=uuid4(),
                project_id=project_id,
                org_id=org_id,
                name=pname,
                username=username,
                encrypted_password=encrypted,
                role_profile=None,
                is_default=False,
                creator_user_id=current_user.id or "",
                # Default to private: the password originated in a private,
                # owner-only filesystem config; preserve that until the user
                # explicitly chooses to make it public via the persona UI.
                visibility=PersonaVisibility.private.value,
                credential_version=1,
                credentials_updated_at=datetime.fromisoformat(now_iso),
                default_app=default_app_raw or None,
            )
            personas.append(new_persona.model_dump(mode="json"))
            personas_added += 1
            personas_changed = True

    if orgs_changed:
        _save_orgs(orgs)
    if personas_changed:
        _save_personas(personas)

    return {
        "synced": True,
        "slug": project_slug,
        "orgs_added": orgs_added,
        "personas_added": personas_added,
    }
