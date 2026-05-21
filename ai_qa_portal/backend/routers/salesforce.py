"""Salesforce schema, org inspector, and SF DX endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import (
    SchemaRequest,
    SchemaResponse,
    SOQLRequest,
    SOQLResponse,
)
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.org_metadata import OrgMetadataService

router = APIRouter(
    prefix="/api/salesforce",
    tags=["salesforce"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/schema/context", response_model=SchemaResponse)
def get_schema_context(body: SchemaRequest):
    try:
        from app_schema import detect_salesforce_objects

        objects = detect_salesforce_objects(body.prompt)
        context = OrgMetadataService.safe_get_schema_context(
            body.prompt,
            body.sandbox_url,
            body.username,
            body.password,
        )
        return SchemaResponse(objects=objects, context=context)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/schema/objects/{object_name}")
def describe_object(object_name: str, sandbox_url: str = "", username: str = "", password: str = ""):
    try:
        service = OrgMetadataService()
        schema = service.describe_object(
            object_name,
            org_key="",
            sandbox_url=sandbox_url,
            username=username,
            password=password,
        )
        summary = OrgMetadataService.safe_get_object_schema_summary(
            object_name,
            sandbox_url,
            username,
            password,
        )
        return {"object": object_name, "schema": schema, "summary": summary}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/soql", response_model=SOQLResponse)
def run_soql(body: SOQLRequest):
    try:
        import sf_dx_bridge
        result = sf_dx_bridge.run_soql_query(body.query)
        records = result.get("result", {}).get("records", []) if isinstance(result, dict) else []
        return SOQLResponse(records=records, total_size=len(records))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/dx/status")
def sf_dx_status():
    try:
        import sf_dx_bridge
        return {"available": sf_dx_bridge.is_available(), "summary": sf_dx_bridge.get_status_summary()}
    except ImportError:
        return {"available": False, "summary": "sf_dx_bridge not installed"}


@router.get("/dx/objects/{object_name}/fields")
def describe_fields(object_name: str):
    try:
        import sf_dx_bridge
        result = sf_dx_bridge.describe_object_fields(object_name)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
