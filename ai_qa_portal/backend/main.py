"""AI QA Portal — unified FastAPI backend with all routes."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import settings
from .services.auth import get_current_user
from .services.db import User, get_db, init_db, list_memberships_for_user

logger = logging.getLogger("ai_qa_portal.main")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from .routers import (
    admin,
    analytics,
    catalog,
    generate,
    heal,
    imports as imports_router,
    integrations,
    invitations,
    llm,
    locators,
    mcp,
    memberships,
    notifications,
    orgs,
    personas,
    projects,
    prompts as prompts_router,
    runs,
    salesforce,
    search as search_router,
    sprints,
    tags,
    test_cases,
    user_stories,
    users,
    visual_regression,
)

app = FastAPI(
    title="AI QA Portal",
    description="Unified production API for AI-driven Salesforce test automation.",
    version="3.0.0",
)

# Allow:
#   - localhost / 127.0.0.1 dev origins
#   - the project's own *.vercel.app deployments (production + per-PR preview
#     URLs always start with the project slug)
#   - any extra origins listed in CORS_ORIGINS or EXTRA_CORS_ORIGINS env vars
# Note: previously we matched ANY *.vercel.app origin, which combined with
# allow_credentials=True meant every Vercel app on the internet could issue
# credentialed CORS requests. Tighten to this project's deployments only.
_explicit_origins = sorted({
    o.strip()
    for raw in (settings.cors_origins, settings.extra_cors_origins)
    for o in raw.split(",")
    if o.strip()
})
_origin_regex = (
    r"^(https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    # Allow local-network dev origins (e.g. http://10.15.0.52:3000)
    r"|https?://10(?:\.\d{1,3}){3}(:\d+)?"
    r"|https?://192\.168(?:\.\d{1,3}){2}(:\d+)?"
    r"|https?://172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}(:\d+)?"
    r"|https://sf-core-automation(-[a-z0-9-]+)?\.vercel\.app)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_explicit_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API-prefix alias middleware (Phase 3 IA audit). Lets clients call
# /api/<bare>/... for any bare-prefixed router (user-stories, sprints,
# test-cases, tags, personas, orgs, run) so the frontend can migrate
# off the legacy bare paths incrementally without coordinated router
# constructor changes. See services/api_alias_middleware.py.
from .services.api_alias_middleware import ApiPrefixAliasMiddleware  # noqa: E402

app.add_middleware(ApiPrefixAliasMiddleware)

app.include_router(projects.router)
app.include_router(memberships.router)
app.include_router(invitations.project_router)
app.include_router(invitations.inv_router)
app.include_router(invitations.me_router)
app.include_router(notifications.router)
app.include_router(orgs.router)
app.include_router(personas.router)
app.include_router(runs.router)
app.include_router(runs.exec_router)
app.include_router(generate.router)
app.include_router(heal.router)
app.include_router(mcp.router)
app.include_router(salesforce.router)
app.include_router(catalog.router)
app.include_router(analytics.router)
app.include_router(locators.router)
app.include_router(sprints.router)
app.include_router(user_stories.router)
app.include_router(test_cases.router)
app.include_router(tags.router)
app.include_router(llm.router)
app.include_router(integrations.router)
app.include_router(imports_router.router)
app.include_router(prompts_router.router)
app.include_router(search_router.router)
app.include_router(visual_regression.router)
app.include_router(users.router)
app.include_router(admin.router)

results_dir = Path(settings.results_dir)
results_dir.mkdir(parents=True, exist_ok=True)
app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")


def _prewarm_rfmcp() -> None:
    """Best-effort: spawn the RF-MCP subprocess so the first Stepwise request
    doesn't pay the 3-8 s subprocess boot cost on its critical path.

    Runs in a background thread so a slow / failing RF-MCP install never
    blocks API startup. Set MCP_PREWARM=0 to disable (recommended for
    `uvicorn --reload` dev loops, where every code change would otherwise
    re-spawn the subprocess).
    """
    try:
        import mcp_bridge
        if mcp_bridge.is_server_running():
            return
        mcp_bridge.start_mcp_server()
        logger.info("RF-MCP pre-warmed at %s", mcp_bridge.mcp_url())
    except Exception as exc:  # noqa: BLE001 -- pre-warm is best-effort
        logger.warning("RF-MCP pre-warm failed (non-fatal): %s", exc)


def _prewarm_memory_model() -> None:
    """Best-effort: ensure the sentence-transformers embedding model RF-MCP
    needs for semantic memory is cached on disk. Once cached, subsequent
    RF-MCP spawns can enable memory without the cold-download wedge.

    Skip silently when sentence-transformers isn't installed -- the
    planner brain falls back to its own DB-backed verified-recipe store.
    """
    try:
        import mcp_bridge
        mcp_bridge.prewarm_memory_model()
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory model prewarm skipped: %s", exc)


def _probe_ollama() -> None:
    """Check whether a local Ollama server is reachable. Logs the result
    so operators can see at a glance whether the local-LLM tier is
    active. Best-effort: a failed probe just means the failover chain
    will skip the ``ollama`` provider until ``_ollama_is_reachable``
    re-checks (every 60s, lazily, on the next call).
    """
    try:
        import requests

        from ai_bridge import _ollama_base_url, _ollama_is_reachable

        if not _ollama_is_reachable(force=True):
            logger.info(
                "Ollama (local LLM) not detected at %s; "
                "failover chain will skip the local tier until it's started. "
                "See README 'Local LLM setup' for install instructions.",
                _ollama_base_url(),
            )
            return

        # Reachable: also list available models so the operator sees
        # what's pulled and ready to use.
        try:
            resp = requests.get(f"{_ollama_base_url()}/api/tags", timeout=2.0)
            tags = resp.json().get("models") or []
            names = [m.get("name") for m in tags if m.get("name")]
            logger.info(
                "Ollama (local LLM) detected at %s; models available: %s",
                _ollama_base_url(),
                ", ".join(names) if names else "(none pulled yet -- run `ollama pull qwen2.5-coder:7b`)",
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.info("Ollama detected at %s; model list unavailable.", _ollama_base_url())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Ollama probe skipped: %s", exc)


@app.on_event("startup")
def _on_startup() -> None:
    init_db()
    # Seed system prompt templates -- idempotent + content-aware. Safe
    # to call on every boot; appends a new system version only when a
    # seed .md changed since last run (release upgrade).
    try:
        from .services.db import SessionLocal
        from .services.prompt_registry import seed_system_templates
        db = SessionLocal()
        try:
            seed_system_templates(db)
        finally:
            db.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("prompt seed failed at startup: %s", exc)
    if os.environ.get("MCP_PREWARM", "1").strip() not in ("0", "false", "False", ""):
        threading.Thread(target=_prewarm_rfmcp, name="rfmcp-prewarm", daemon=True).start()
    # Probe Ollama once at startup (non-blocking via thread). The
    # reachability cache then drives the failover chain build for the
    # next 60s; subsequent probes happen lazily inside _has_api_key.
    threading.Thread(target=_probe_ollama, name="ollama-probe", daemon=True).start()
    # Pre-cache the sentence-transformers embedding model in the
    # background. Once it's on disk we can enable RF-MCP semantic memory
    # without the cold-download wedge that would otherwise hang
    # manage_session(init) for ~5 minutes on first call.
    if os.environ.get("MCP_MEMORY_PREWARM", "1").strip() not in ("0", "false", "False", ""):
        threading.Thread(
            target=_prewarm_memory_model,
            name="rfmcp-memory-prewarm",
            daemon=True,
        ).start()

    # Start APScheduler and re-register every enabled local schedule.
    # github_actions schedules are intentionally not registered here --
    # their cron lives in the connected repo's workflow YAML.
    try:
        from .services import scheduler as _scheduler_service
        from .services.db import SessionLocal

        _scheduler_service.start()
        if settings.scheduler_enabled:
            with SessionLocal() as session:
                reloaded = _scheduler_service.reload_all(session)
            logger.info("APScheduler: re-registered %d local schedule(s)", reloaded)
    except Exception as exc:  # noqa: BLE001 -- scheduler failure should never block boot
        logger.warning("Scheduler startup failed (non-fatal): %s", exc)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    try:
        from .services import scheduler as _scheduler_service
        _scheduler_service.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        from .services.generation_worker import shutdown_generation_pool
        shutdown_generation_pool()
    except Exception:  # noqa: BLE001
        pass


@app.get("/")
def root():
    return {"name": "AI QA Portal", "version": "3.0.0", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/me")
def whoami(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the currently authenticated user + their per-project memberships.
    Frontend uses this to render the profile menu, gate role-aware UI, and
    detect 401/403 (unauthenticated / wrong domain)."""
    payload = current_user.to_dict()
    payload["memberships"] = [
        m.to_dict() for m in list_memberships_for_user(db, current_user.id)
    ]
    return payload
