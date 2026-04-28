"""AI QA Portal — unified FastAPI backend with all routes."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import settings
from .services.auth import get_current_user
from .services.db import User, get_db, init_db, list_memberships_for_user

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from .routers import (
    admin,
    analytics,
    catalog,
    generate,
    invitations,
    llm,
    locators,
    mcp,
    memberships,
    notifications,
    orgs,
    personas,
    projects,
    runs,
    salesforce,
    sprints,
    user_stories,
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
app.include_router(llm.router)
app.include_router(mcp.router)
app.include_router(salesforce.router)
app.include_router(catalog.router)
app.include_router(analytics.router)
app.include_router(locators.router)
app.include_router(user_stories.router)
app.include_router(user_stories.test_cases_router)
app.include_router(user_stories.tags_router)
app.include_router(sprints.router)
app.include_router(admin.router)

results_dir = Path(settings.results_dir)
results_dir.mkdir(parents=True, exist_ok=True)
app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")


@app.on_event("startup")
def _on_startup() -> None:
    init_db()


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
