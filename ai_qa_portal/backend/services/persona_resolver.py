"""Compatibility persona resolver used by `routers.runs`."""

from __future__ import annotations

from uuid import UUID

from ..models.persona import Persona


class PersonaResolver:
    """Pick a persona for a run request.

    Resolution order:
    1. Explicit UI persona id, when present and found.
    2. Project+org default persona (`is_default=True`).
    3. First persona matching project+org.
    """

    def resolve(
        self,
        *,
        project_id: UUID,
        org_id: UUID,
        prompt: str,  # kept for compatibility with existing caller signature
        ui_persona_id: UUID | None,
        all_personas: list[Persona],
    ) -> tuple[Persona, str]:
        _ = prompt
        candidates = [p for p in all_personas if p.project_id == project_id and p.org_id == org_id]
        if not candidates:
            raise ValueError("No personas available for this project and org")

        if ui_persona_id:
            explicit = next((p for p in candidates if p.id == ui_persona_id), None)
            if not explicit:
                raise ValueError("Selected persona is not available for this project and org")
            return explicit, "explicit"

        default = next((p for p in candidates if p.is_default), None)
        if default:
            return default, "default"
        return candidates[0], "fallback"
