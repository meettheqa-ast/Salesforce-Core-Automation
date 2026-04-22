"""LLM interaction endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import (
    FailureAnalysis,
    FailureAnalysisResponse,
    LLMChatRequest,
    LLMChatResponse,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.post("/chat", response_model=LLMChatResponse)
def chat(body: LLMChatRequest):
    """Send a message to the configured LLM and return the response."""
    try:
        from ai_bridge import call_llm, hydrate_llm_env
        import os

        hydrate_llm_env()
        if body.provider:
            os.environ["LLM_PROVIDER"] = body.provider

        response = call_llm(body.system_prompt, body.user_message)
        provider = os.environ.get("LLM_PROVIDER", "gemini")
        return LLMChatResponse(content=response, provider=provider)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analyze-failure", response_model=FailureAnalysisResponse)
def analyze_failure(body: FailureAnalysis):
    """Ask the LLM to analyze a test failure and suggest fixes."""
    try:
        from ai_bridge import analyze_test_failure

        analysis = analyze_test_failure(body.robot_code, body.error_message)
        return FailureAnalysisResponse(analysis=analysis)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/plan-steps")
def plan_robot_steps(prompt: str):
    """Decompose a natural language prompt into Robot Framework keyword steps."""
    try:
        from ai_bridge import break_prompt_into_steps

        steps = break_prompt_into_steps(prompt)
        return {"steps": steps}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/providers")
def list_providers():
    """List available LLM providers."""
    try:
        from ai_bridge import PROVIDER_LABELS
        return {
            "providers": [
                {"id": k, "label": v}
                for k, v in PROVIDER_LABELS.items()
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
