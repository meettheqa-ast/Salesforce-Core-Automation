"""AI QA Portal — unified FastAPI backend with all routes."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from .routers import (
    analytics,
    catalog,
    generate,
    llm,
    locators,
    mcp,
    orgs,
    personas,
    projects,
    runs,
    salesforce,
    user_stories,
)

app = FastAPI(
    title="AI QA Portal",
    description="Unified production API for AI-driven Salesforce test automation.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(orgs.router)
app.include_router(personas.router)
app.include_router(runs.router)
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

results_dir = REPO_ROOT / "Results"
if results_dir.exists():
    app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")


@app.get("/")
def root():
    return {"name": "AI QA Portal", "version": "3.0.0", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}
