"""Robot bridge library for runtime form-healing."""

from __future__ import annotations

import os
from typing import Any

from robot.api import logger
from robot.api.deco import keyword


class HealBridgeLibrary:
    ROBOT_LIBRARY_SCOPE = "TEST SUITE"

    @keyword("Heal Bridge Save And Heal")
    def heal_bridge_save_and_heal(
        self,
        *,
        sobject: str = "Lead",
        max_attempts: int = 3,
        duplicate_strategy: str = "regenerate",
        session_id: str = "",
        save_action: str = "Save",
        project_slug: str = "",
        run_id: str = "",
        api_base_url: str = "",
    ) -> dict[str, Any]:
        _ = max_attempts  # Runtime budget is controlled by backend env vars.
        if not session_id:
            logger.info("Heal bridge: no session_id supplied; returning skipped for Robot fallback.")
            return {"outcome": "skipped", "reason": "missing_session_id"}

        # Preferred mode: in-process call into backend engine.
        if os.environ.get("HEAL_BRIDGE_TRANSPORT", "inprocess").strip().lower() == "inprocess":
            try:
                from ai_qa_portal.backend.services.form_healer import FormHealer

                result = FormHealer().heal_save(
                    session_id=session_id,
                    sobject=sobject,
                    save_action=save_action,
                    duplicate_strategy=duplicate_strategy,
                    project_slug=project_slug or None,
                    run_id=run_id or None,
                )
                return {
                    "outcome": result.outcome,
                    "reason": result.reason,
                    "attempts": len(result.attempts),
                    "healed_fields": list(result.healed_fields),
                }
            except Exception as exc:
                logger.warn(f"Heal bridge in-process failed: {exc}")

        # Fallback: HTTP endpoint when backend is reachable and token is available.
        base = (api_base_url or os.environ.get("API_BASE_URL") or "http://localhost:8000").rstrip("/")
        token = os.environ.get("API_BEARER_TOKEN", "").strip()
        try:
            import requests

            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = requests.post(
                f"{base}/api/heal/save",
                headers=headers,
                json={
                    "session_id": session_id,
                    "sobject": sobject,
                    "save_action": save_action,
                    "duplicate_strategy": duplicate_strategy,
                    "project_slug": project_slug or None,
                    "run_id": run_id or None,
                },
                timeout=45,
            )
            if resp.ok:
                return dict(resp.json())
            logger.warn(f"Heal bridge HTTP returned {resp.status_code}: {resp.text[:300]}")
            return {"outcome": "skipped", "reason": f"http_{resp.status_code}"}
        except Exception as exc:
            logger.warn(f"Heal bridge HTTP failed: {exc}")
            return {"outcome": "skipped", "reason": "http_unreachable"}

