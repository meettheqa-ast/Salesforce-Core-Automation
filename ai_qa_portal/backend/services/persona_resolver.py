from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from ..models.persona import Persona

logger = logging.getLogger(__name__)


class PersonaResolver:
    TRIGGER_PHRASES = ["login as", "log in as", "run as", "using", "acting as", "as the"]

    def resolve(
        self,
        project_id: UUID,
        org_id: UUID,
        prompt: str,
        ui_persona_id: Optional[UUID],
        all_personas: list[Persona],
    ) -> tuple[Persona, str]:
        """Return (resolved_persona, resolution_method).

        resolution_method is one of: ui_selection, prompt_extraction,
        org_default, system_admin_fallback.
        """
        scoped = [
            p for p in all_personas
            if p.project_id == project_id and p.org_id == org_id
        ]

        if ui_persona_id:
            match = next((p for p in scoped if p.id == ui_persona_id), None)
            if match:
                return match, "ui_selection"

        candidate = self._extract_from_prompt(prompt)
        if candidate:
            fuzzy_match = self._fuzzy_match(candidate, scoped)
            if fuzzy_match:
                return fuzzy_match, "prompt_extraction"
            else:
                logger.warning(
                    "Persona '%s' mentioned in prompt but not found in org %s. "
                    "Available: %s",
                    candidate,
                    org_id,
                    [p.name for p in scoped],
                )

        org_default = next((p for p in scoped if p.is_default), None)
        if org_default:
            return org_default, "org_default"

        sysadmin = next(
            (p for p in scoped if p.name.lower() in ("system admin", "system administrator")),
            None,
        )
        if sysadmin:
            return sysadmin, "system_admin_fallback"

        raise ValueError(
            f"No persona could be resolved for project {project_id}, org {org_id}. "
            "Configure at least one persona with is_default=True or name='System Admin'."
        )

    def _extract_from_prompt(self, prompt: str) -> Optional[str]:
        """Extract persona name following a trigger phrase."""
        lowered = prompt.lower()
        for phrase in self.TRIGGER_PHRASES:
            idx = lowered.find(phrase)
            if idx != -1:
                after = prompt[idx + len(phrase):].strip()
                candidate = " ".join(after.split()[:4])
                return candidate.strip(".,;:")
        return None

    def _fuzzy_match(self, candidate: str, personas: list[Persona]) -> Optional[Persona]:
        """Return best persona match within Levenshtein distance 2."""
        try:
            from Levenshtein import distance
        except ImportError:
            return self._simple_match(candidate, personas)

        candidate_lower = candidate.lower()
        best: Optional[Persona] = None
        best_dist = 3
        for p in personas:
            d = distance(candidate_lower, p.name.lower())
            if d < best_dist:
                best_dist = d
                best = p
        return best

    @staticmethod
    def _simple_match(candidate: str, personas: list[Persona]) -> Optional[Persona]:
        """Fallback when python-Levenshtein is not installed."""
        cl = candidate.lower()
        for p in personas:
            if cl == p.name.lower() or cl in p.name.lower() or p.name.lower() in cl:
                return p
        return None
