"""Keyword catalog endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ai_qa_portal.backend.config import CATALOG_PATH
from ai_qa_portal.backend.models.schemas import CatalogResponse, KeywordEntry

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/keywords", response_model=CatalogResponse)
def list_keywords():
    if not CATALOG_PATH.exists():
        return CatalogResponse(keywords=[], count=0)
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = data.get("keywords", [])
    keywords = [
        KeywordEntry(
            keyword_name=e.get("keyword_name", ""),
            arguments=e.get("arguments", []),
            documentation=e.get("documentation", ""),
            tags=e.get("tags", []),
            source_file=e.get("source_file", ""),
        )
        for e in entries
    ]
    return CatalogResponse(
        keywords=keywords,
        count=len(keywords),
        generated_at=data.get("generated_at", ""),
    )


@router.post("/rebuild")
def rebuild_catalog():
    try:
        import sys
        sys.path.insert(0, str(CATALOG_PATH.parent))
        from app_catalog import rebuild_keyword_catalog
        rebuild_keyword_catalog()
        return {"status": "rebuilt", "path": str(CATALOG_PATH)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
