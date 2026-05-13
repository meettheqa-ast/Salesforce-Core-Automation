"""LLM utility endpoints used by settings + diagnostics UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_qa_portal.backend.models.schemas import (
    FailureAnalysis,
    FailureAnalysisResponse,
    LLMChatRequest,
    LLMChatResponse,
)
from ai_qa_portal.backend.services.auth import get_current_user

router = APIRouter(
    prefix="/api/llm",
    tags=["llm"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/providers")
def list_llm_providers():
    from ai_bridge import LLM_PROVIDERS, PROVIDER_LABELS, _default_primary_provider, _has_api_key  # type: ignore[attr-defined]

    primary = _default_primary_provider()
    out: list[dict] = []
    for pid, (_key_env, model_env, default_model) in LLM_PROVIDERS.items():
        out.append(
            {
                "id": pid,
                "label": PROVIDER_LABELS.get(pid, pid),
                "configured": bool(_has_api_key(pid)),
                "model": default_model,
                "model_env": model_env,
                "is_primary": pid == primary,
            }
        )
    return {"providers": out, "primary": primary}


@router.post("/chat", response_model=LLMChatResponse)
def llm_chat(body: LLMChatRequest):
    from ai_bridge import LLM_PROVIDERS, call_llm

    content = call_llm(
        body.system_prompt or "",
        body.user_message,
        provider=body.provider,
    )
    provider = (body.provider or "").strip().lower()
    if not provider:
        from ai_bridge import _default_primary_provider  # type: ignore[attr-defined]

        provider = _default_primary_provider()
    provider_info = LLM_PROVIDERS.get(provider)
    model = provider_info[2] if provider_info else ""
    return LLMChatResponse(content=content, provider=provider, model=model)


@router.post("/analyze-failure", response_model=FailureAnalysisResponse)
def analyze_failure(body: FailureAnalysis):
    from ai_bridge import analyze_test_failure

    analysis = analyze_test_failure(
        test_name=(body.prompt or "failed test"),
        error_message=body.error_message or "unknown error",
    )
    return FailureAnalysisResponse(analysis=analysis, suggested_fix="")
