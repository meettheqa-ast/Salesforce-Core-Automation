"""Tool-calling Stepwise planner (OpenAI-style function calling).

This is an alternative to ``ai_bridge.break_prompt_into_steps`` enabled
via ``MCP_PLANNER_MODE=function_calling``. Instead of dumping the full
catalog into the prompt and asking the LLM to emit a JSON array, we
expose three tools:

* ``find_keyword(query)`` -- top 5 matches from the catalog (qualified
  name + signature hint + one-line doc).
* ``get_keyword_signature(name)`` -- detailed signature + side-effect
  tags for a single keyword the model is considering.
* ``submit_plan(steps)`` -- final answer; the conversation ends here.

The model iterates: search → inspect → search → ... → submit. The
catalog tokens move from the system prompt into selective tool
responses, which is roughly 4x cheaper on prompts where the model
ends up looking at < 20 keywords (which is most of them).

We DO NOT require the active LLM provider to support function calling.
When OpenAI's tools API is unavailable (Anthropic / Cohere / Ollama
in vanilla mode) the caller falls back to the JSON-array planner.

Provider compatibility:
- OpenAI / Groq / Together / OpenRouter / Mistral: native support.
- Gemini: separate ``tools=`` schema; we translate.
- Ollama (recent versions): supports tools on Llama 3.1+ models.
- Anthropic: separate ``tools`` API; we translate.
- Cursor: routed through the SDK sidecar; supported.
- Cohere: tool-use API differs; we don't attempt and let the caller fall back.

In practice we only enable this path when ``MCP_PLANNER_MODE=function_calling``
AND the active provider is OpenAI-compatible (the most common shape).
Operators on other providers should leave the env flag at its default.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("ai_qa_portal.planner_tools")


# --- Tool definitions (OpenAI tools schema) -------------------------------


PLANNER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_keyword",
            "description": (
                "Search the project's keyword catalog. Returns up to 5 closest "
                "matches by name. Use this whenever you're not 100% sure a "
                "keyword exists or what its exact qualified name is."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text search. Can be a partial keyword name "
                            "('login'), an action ('open lead'), or a library "
                            "prefix ('SalesPO')."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 15,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_keyword_signature",
            "description": (
                "Look up the full signature of a specific keyword: "
                "positional vs named arguments, doc summary, and "
                "side-effect tags. Use BEFORE you decide to call a "
                "keyword you're not sure about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Qualified ('SalesPO.Create A New Lead') or bare "
                            "('Create A New Lead') name."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": (
                "Submit the final ordered plan. Once you call this, the "
                "conversation ends and the runtime begins executing your "
                "plan. Do NOT call any other tool after this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string"},
                                "args": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["keyword"],
                        },
                    },
                },
                "required": ["steps"],
            },
        },
    },
]


# --- Tool implementations -------------------------------------------------


def _tool_find_keyword(args: dict) -> str:
    from ai_qa_portal.backend.services import planner_quality

    query = (args.get("query") or "").strip()
    limit = int(args.get("limit") or 5)
    if not query:
        return json.dumps({"error": "query is required"})
    matches = planner_quality.closest_keywords(query, n=max(1, min(limit, 15)))
    sigs = planner_quality.signature_index()
    out: list[dict] = []
    for canonical in matches:
        sig = sigs.get(canonical.lower())
        if sig is None:
            out.append({"name": canonical})
            continue
        out.append({
            "name": sig.qualified,
            "signature": sig.signature_hint(),
            "doc": sig.doc_summary,
            "side_effects": list(sig.side_effects),
        })
    return json.dumps({"matches": out}, ensure_ascii=False)


def _tool_get_keyword_signature(args: dict) -> str:
    from ai_qa_portal.backend.services import planner_quality

    name = (args.get("name") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"})
    canonical = planner_quality.resolve_keyword(name)
    if canonical is None:
        return json.dumps({
            "error": f"keyword '{name}' not in catalog",
            "suggestions": planner_quality.closest_keywords(name),
        })
    sig = planner_quality.signature_index().get(canonical.lower())
    if sig is None:
        return json.dumps({"name": canonical, "signature": "", "doc": "", "side_effects": []})
    return json.dumps({
        "name": sig.qualified,
        "library": sig.library,
        "positional": list(sig.positional),
        "named": list(sig.named),
        "var_args": sig.var_args,
        "var_named": sig.var_named,
        "signature_hint": sig.signature_hint(),
        "doc": sig.doc_summary,
        "side_effects": list(sig.side_effects),
    }, ensure_ascii=False)


_TOOL_IMPLS = {
    "find_keyword": _tool_find_keyword,
    "get_keyword_signature": _tool_get_keyword_signature,
}


# --- Provider compatibility ----------------------------------------------


def supported_provider() -> str | None:
    """Return the active LLM provider name when it supports OpenAI tools.

    Conservative: only providers we've actually tested function-calling
    against return a name. The caller falls back to the JSON-array
    planner when this returns ``None``.
    """
    try:
        from ai_bridge import _default_primary_provider, _has_api_key
    except Exception:  # noqa: BLE001
        return None

    explicit = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    primary = explicit or _default_primary_provider()
    # OpenAI API + drop-in compatibles are tested and supported.
    OPENAI_COMPAT = {"openai", "groq", "together", "openrouter", "mistral"}
    if primary in OPENAI_COMPAT and _has_api_key(primary):
        return primary
    return None


def is_enabled() -> bool:
    """True when the operator has opted in AND the provider supports tools."""
    mode = (os.environ.get("MCP_PLANNER_MODE") or "json").strip().lower()
    if mode != "function_calling":
        return False
    return supported_provider() is not None


# --- Plan loop ------------------------------------------------------------


def plan_with_tools(
    user_prompt: str,
    *,
    system_prompt: str,
    default_app: str = "",
    project_slug: str | None = None,
    max_iterations: int = 8,
) -> list[dict] | None:
    """Run the tool-calling planner. Returns the submitted steps, or
    ``None`` when the provider isn't compatible / the model never
    submitted a plan within ``max_iterations``.
    """
    provider = supported_provider()
    if provider is None:
        return None

    try:
        from ai_bridge import (
            LLM_PROVIDERS,
            _get_provider_key_and_model,
            hydrate_llm_env,
        )
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        logger.debug("plan_with_tools: dependencies unavailable: %s", exc)
        return None

    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model(provider)
    base_url = None
    base_url_map = {
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "mistral": "https://api.mistral.ai/v1",
    }
    if provider in base_url_map:
        base_url = base_url_map[provider]

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)

    system_msg = {
        "role": "system",
        "content": (
            system_prompt
            + "\n\n"
            "## Tool-use mode\n\n"
            "You can call `find_keyword(query)` and "
            "`get_keyword_signature(name)` as many times as you need to "
            "explore the catalog before deciding on a plan. "
            "Once you have a final plan, call `submit_plan(steps)` and "
            "stop. Do not output a JSON array as a normal message -- "
            "only `submit_plan` ends the planning round."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            (f"Project: {project_slug}\n" if project_slug else "")
            + (f"Persona default app: {default_app}\n" if default_app else "")
            + f"\nUser request:\n{user_prompt.strip()}"
        ),
    }

    messages: list[dict[str, Any]] = [system_msg, user_msg]
    submitted: list[dict] | None = None

    for _ in range(max(1, max_iterations)):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=PLANNER_TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan_with_tools: provider call failed: %s", exc)
            return None

        choice = completion.choices[0]
        message = choice.message
        # Append the assistant message verbatim; OpenAI requires the tool
        # role messages to follow the assistant message that contained
        # the corresponding tool_calls.
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (message.tool_calls or [])
            ],
        })
        tool_calls = message.tool_calls or []
        if not tool_calls:
            # The model gave up on tools and returned text; bail out so
            # the caller can fall back to the JSON planner.
            logger.debug("plan_with_tools: model emitted text without a tool_call")
            return None

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "submit_plan":
                steps = args.get("steps") or []
                if isinstance(steps, list):
                    submitted = [s for s in steps if isinstance(s, dict)]
                # Even on a malformed submit, append a tool response so the
                # API contract is satisfied if we keep iterating.
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"received": True}),
                })
                continue
            impl = _TOOL_IMPLS.get(name)
            if impl is None:
                response = json.dumps({"error": f"unknown tool {name!r}"})
            else:
                try:
                    response = impl(args)
                except Exception as exc:  # noqa: BLE001
                    response = json.dumps({"error": str(exc)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": response,
            })
        if submitted is not None:
            return submitted

    logger.warning(
        "plan_with_tools: exhausted %s iterations without a submit_plan call",
        max_iterations,
    )
    return None
