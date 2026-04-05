"""
Shared paths, session keys, and optional dependency flags for the Streamlit app.
"""

from __future__ import annotations

from pathlib import Path

try:
    import project_manager as _pm
except Exception:
    _pm = None  # type: ignore[assignment]

_HAS_WORKSPACE = _pm is not None

try:
    import org_inspector as _org_inspector_mod
except Exception:
    _org_inspector_mod = None  # type: ignore[assignment]

_HAS_ORG_INSPECTOR = _org_inspector_mod is not None

try:
    from smoke_templates import detect_smoke_intent as _detect_smoke_fn
    from smoke_templates import get_smoke_prompt as _smoke_prompt_fn
except Exception:
    _detect_smoke_fn = None  # type: ignore[assignment]
    _smoke_prompt_fn = None  # type: ignore[assignment]

_HAS_SMOKE = _detect_smoke_fn is not None and _smoke_prompt_fn is not None

ROOT = Path(__file__).resolve().parent
GENERATED_SUITE = ROOT / "Tests" / "Generated" / "temp_test.robot"
CATALOG_JSON_PATH = ROOT / "keyword_catalog.json"

CLARIFY_SESSION_KEY = "clarify_context"

# Human-in-the-loop AI generation (review before save/run)
PENDING_ROBOT_EDITOR_KEY = "pending_robot_editor"
PENDING_GEN_CTX_KEY = "pending_gen_ctx"

# Limits for LLM context size (full row count still passed in summary line).
CSV_MARKDOWN_MAX_ROWS = 50
CSV_JSON_MAX_ROWS = 120
