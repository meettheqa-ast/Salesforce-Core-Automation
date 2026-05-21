"""Duplicate-detection + per-duplicate strategy for the import wizard.

Three detection keys, tried in order:

  1. ``external_id``  -- exact match against TestCase.external_id.
     Most precise; only fires when the source row carried an
     external_id (Jira key, Zephyr id, etc.) the user mapped.

  2. ``title + story_id`` -- case-insensitive title match scoped to
     the target story. Used when no external_id is available and
     the import has a single target story.

  3. ``title + project_id`` -- title match across the whole project.
     Used when story_id is per-row (mapped from a 'Story ID' column)
     and we can't scope to one story cheaply.

The three strategies (skip / overwrite / create_new) are applied at
commit time -- this module only DETECTS; the router decides what to
do with the verdict.

Note: this engine reads test cases out of the project_manager JSON
store (NOT SQL). That's the same store the rest of the TC pipeline
uses, and avoids a parallel index just for dedupe.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

logger = logging.getLogger("ai_qa_portal.import_dedupe")

# Single shared store -- the same instance every router uses. Tests
# monkeypatch this via ``import_dedupe._store`` so they hit an
# isolated fixture instead of the real data dir.
_store = JsonFileBackend(settings.data_dir)


DuplicateStrategy = Literal["skip", "overwrite", "create_new"]
DetectionReason = Literal["external_id_match", "title_match_in_story", "title_match_in_project"]


@dataclass
class DuplicateVerdict:
    """Result of checking a single MappedRow against existing TCs.

    ``existing_id`` is the UUID of the colliding TC (None when there
    is no match). ``reason`` records WHICH heuristic fired so the
    UI can show "matched by external_id" vs "matched by title".
    """

    is_duplicate: bool
    existing_id: str | None = None
    reason: DetectionReason | None = None


def _normalise_title(title: str) -> str:
    """Title comparison: case-insensitive + whitespace-collapsed.
    Punctuation is preserved because TC titles often encode it
    meaningfully ('Login -- happy path' != 'Login happy path')."""
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


def find_duplicate(
    *,
    external_id: str | None,
    title: str,
    target_story_id: str | None,
    target_project_id: str | None,
) -> DuplicateVerdict:
    """Run the three checks in order; return on first hit.

    The lookups go through ``_store`` so tests can swap the backend.
    Every miss returns is_duplicate=False so the caller treats the
    row as new.
    """
    # --- 1. external_id ----------------------------------------------
    if external_id:
        match = _find_by_external_id(external_id, project_id=target_project_id)
        if match:
            return DuplicateVerdict(
                is_duplicate=True,
                existing_id=match,
                reason="external_id_match",
            )

    norm_title = _normalise_title(title)
    if not norm_title:
        # An empty title can't dedupe against anything cleanly. Let the
        # commit validator reject it as missing-required-field instead.
        return DuplicateVerdict(is_duplicate=False)

    # --- 2. title within a single target story ----------------------
    if target_story_id:
        for row in _store.get_test_cases_by_story(target_story_id):
            if _normalise_title(str(row.get("title", ""))) == norm_title:
                return DuplicateVerdict(
                    is_duplicate=True,
                    existing_id=str(row.get("id")),
                    reason="title_match_in_story",
                )

    # --- 3. title across the whole project --------------------------
    if target_project_id:
        idx = _store.read(f"test_case_ids_project:{target_project_id}")
        for tid in idx.get("ids", []):
            row = _store.read(f"test_case:{tid}")
            if row and _normalise_title(str(row.get("title", ""))) == norm_title:
                return DuplicateVerdict(
                    is_duplicate=True,
                    existing_id=str(row.get("id")),
                    reason="title_match_in_project",
                )

    return DuplicateVerdict(is_duplicate=False)


def _find_by_external_id(external_id: str, *, project_id: str | None) -> str | None:
    """Scan TestCases in the target project for a matching external_id.

    We scope to the project to avoid cross-tenant collisions (two
    different projects may have imported the same Jira issue id under
    independent batches). When no project_id is supplied we widen to a
    full scan via the rare-but-possible orphan id case.
    """
    if not external_id:
        return None
    if project_id:
        idx = _store.read(f"test_case_ids_project:{project_id}")
        candidates = list(idx.get("ids", []))
    else:
        # No project scope -- caller is asking the global question.
        # JsonFileBackend doesn't index by external_id so we can't
        # answer this cheaply. Return None and let the title-based
        # fallbacks handle it.
        return None
    for tid in candidates:
        row = _store.read(f"test_case:{tid}")
        if row and str(row.get("external_id") or "") == external_id:
            return str(row.get("id"))
    return None
