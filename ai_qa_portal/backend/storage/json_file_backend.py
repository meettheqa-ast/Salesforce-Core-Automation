from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from .base import StorageBackend


def _safe_key(key: str) -> str:
    """Filesystem-safe key (Windows forbids ':' in paths)."""
    return (
        key.replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("?", "_")
        .replace("*", "_")
    )


class JsonFileBackend(StorageBackend):
    def __init__(self, data_dir: str = "./data"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{_safe_key(key)}.json"

    def read(self, key: str) -> dict[str, Any]:
        p = self._path(key)
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def write(self, key: str, data: dict[str, Any]) -> None:
        p = self._path(key)
        p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    # --- User stories / test cases / tags (additive) -----------------

    def save_user_story(self, story: dict[str, Any]) -> None:
        sid = str(story["id"])
        # Detect sprint reassignment so we can keep the per-sprint index
        # in sync. If the story had a different sprint_id before, drop it
        # from the old index; if it has one now, add to the new index.
        old_row = self.read(f"user_story:{sid}")
        old_sprint = old_row.get("sprint_id") if old_row else None
        new_sprint = story.get("sprint_id")

        self.write(f"user_story:{sid}", story)

        pid = str(story["project_id"])
        idx_key = f"user_stories_by_project:{pid}"
        idx = self.read(idx_key)
        ids: list[str] = list(idx.get("ids", []))
        if sid not in ids:
            ids.insert(0, sid)
        self.write(idx_key, {"ids": ids})

        # Sprint index maintenance. The sprint_id is optional on
        # UserStory; both branches no-op cleanly when the field is null.
        if old_sprint and old_sprint != new_sprint:
            old_key = f"user_stories_by_sprint:{old_sprint}"
            old_idx = self.read(old_key)
            old_ids = [x for x in old_idx.get("ids", []) if x != sid]
            self.write(old_key, {"ids": old_ids})
        if new_sprint:
            sp_key = f"user_stories_by_sprint:{new_sprint}"
            sp_idx = self.read(sp_key)
            sp_ids: list[str] = list(sp_idx.get("ids", []))
            if sid not in sp_ids:
                sp_ids.insert(0, sid)
            self.write(sp_key, {"ids": sp_ids})

    def get_user_story(self, story_id: UUID) -> dict[str, Any]:
        data = self.read(f"user_story:{story_id}")
        if not data:
            raise KeyError(str(story_id))
        return data

    def list_user_stories(self, project_id: UUID) -> list[dict[str, Any]]:
        idx = self.read(f"user_stories_by_project:{project_id}")
        out: list[dict[str, Any]] = []
        for sid in idx.get("ids", []):
            try:
                row = self.read(f"user_story:{sid}")
            except (OSError, json.JSONDecodeError):
                continue
            if row:
                out.append(row)
        return out

    # --- Sprint persistence (parallels user_story) -------------------

    def save_sprint(self, sprint: dict[str, Any]) -> None:
        sid = str(sprint["id"])
        self.write(f"sprint:{sid}", sprint)
        pid = str(sprint["project_id"])
        idx_key = f"sprints_by_project:{pid}"
        idx = self.read(idx_key)
        ids: list[str] = list(idx.get("ids", []))
        if sid not in ids:
            ids.insert(0, sid)
        self.write(idx_key, {"ids": ids})

    def get_sprint(self, sprint_id: UUID) -> dict[str, Any]:
        data = self.read(f"sprint:{sprint_id}")
        if not data:
            raise KeyError(str(sprint_id))
        return data

    def list_sprints(self, project_id: UUID) -> list[dict[str, Any]]:
        idx = self.read(f"sprints_by_project:{project_id}")
        out: list[dict[str, Any]] = []
        for sid in idx.get("ids", []):
            row = self.read(f"sprint:{sid}")
            if row:
                out.append(row)
        return out

    def get_user_stories_by_sprint(self, sprint_id: UUID) -> list[dict[str, Any]]:
        """Read the per-sprint story index. Returns rows in insertion
        order (newest first since save_user_story uses `insert(0, ...)`)."""
        idx = self.read(f"user_stories_by_sprint:{sprint_id}")
        out: list[dict[str, Any]] = []
        for sid in idx.get("ids", []):
            row = self.read(f"user_story:{sid}")
            if row:
                out.append(row)
        return out

    def save_test_case(self, tc: dict[str, Any]) -> None:
        tid = str(tc["id"])
        sid = str(tc["user_story_id"])
        pid = str(tc["project_id"])
        self.write(f"test_case:{tid}", tc)

        sidx = self.read(f"test_cases_by_story:{sid}")
        s_ids: list[str] = list(sidx.get("ids", []))
        if tid not in s_ids:
            s_ids.insert(0, tid)
        self.write(f"test_cases_by_story:{sid}", {"ids": s_ids})

        pidx = self.read(f"test_case_ids_project:{pid}")
        p_ids: list[str] = list(pidx.get("ids", []))
        if tid not in p_ids:
            p_ids.insert(0, tid)
        self.write(f"test_case_ids_project:{pid}", {"ids": p_ids})

        for tag_name in tc.get("tags") or []:
            if not tag_name:
                continue
            tkey = f"test_case_ids_project_tag:{pid}:{tag_name}"
            tidx = self.read(tkey)
            t_ids: list[str] = list(tidx.get("ids", []))
            if tid not in t_ids:
                t_ids.insert(0, tid)
            self.write(tkey, {"ids": t_ids})

    def get_test_case(self, test_case_id: UUID) -> dict[str, Any]:
        data = self.read(f"test_case:{test_case_id}")
        if not data:
            raise KeyError(str(test_case_id))
        return data

    def get_test_cases_by_story(self, user_story_id: UUID) -> list[dict[str, Any]]:
        idx = self.read(f"test_cases_by_story:{user_story_id}")
        out: list[dict[str, Any]] = []
        for tid in idx.get("ids", []):
            row = self.read(f"test_case:{tid}")
            if row:
                out.append(row)
        return out

    def get_test_cases_by_tag(self, project_id: UUID, tag_name: str) -> list[dict[str, Any]]:
        idx = self.read(f"test_case_ids_project_tag:{project_id}:{tag_name}")
        out: list[dict[str, Any]] = []
        for tid in idx.get("ids", []):
            row = self.read(f"test_case:{tid}")
            if not row:
                continue
            if row.get("status") != "approved":
                continue
            tags = row.get("tags") or []
            if tag_name in tags:
                out.append(row)
        return out

    def save_tag(self, tag: dict[str, Any]) -> None:
        pid = str(tag["project_id"])
        name = str(tag["name"])
        self.write(f"tags:{pid}:{name}", tag)

    def list_tags(self, project_id: UUID) -> list[dict[str, Any]]:
        prefix = _safe_key(f"tags:{project_id}:")
        out: list[dict[str, Any]] = []
        for p in self._dir.glob(f"{prefix}*.json"):
            try:
                row = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if row and str(row.get("project_id")) == str(project_id):
                out.append(row)
        return out

    # --- Hard-delete primitives --------------------------------------
    #
    # Soft delete (sprint -> cancelled, story -> archived, test case ->
    # rejected) mutates state in place via ``save_*``. These methods
    # remove the entity AND clean every index entry that referenced it,
    # so the JSON store stays consistent. They are idempotent: deleting
    # an already-removed entity is a no-op.
    #
    # Callers are responsible for the lifecycle gate (refusing to hard
    # delete a record that is not in its soft-deleted state, or that
    # still has live children). This layer just executes the storage
    # mutation cleanly.

    def _delete_file(self, key: str) -> bool:
        p = self._path(key)
        if not p.exists():
            return False
        try:
            p.unlink()
            return True
        except OSError:
            return False

    def _drop_from_index(self, idx_key: str, target_id: str) -> None:
        idx = self.read(idx_key)
        ids = [x for x in idx.get("ids", []) if x != target_id]
        # If nothing remains, persist an empty list rather than deleting
        # the index file -- keeps the shape stable for callers that
        # always read with `.get("ids", [])`.
        self.write(idx_key, {"ids": ids})

    def hard_delete_sprint(self, sprint_id: UUID) -> bool:
        """Remove the sprint row and drop it from
        ``sprints_by_project:<pid>``. Does NOT touch stories that point
        at this sprint -- the caller (router) is expected to clear
        ``sprint_id`` on those rows BEFORE calling this, the same way
        the existing soft delete does it. We do clean the
        ``user_stories_by_sprint:<sid>`` reverse index here so a stale
        lookup doesn't return ghosts."""
        sid = str(sprint_id)
        existing = self.read(f"sprint:{sid}")
        if not existing:
            return False
        pid = str(existing.get("project_id") or "")
        if pid:
            self._drop_from_index(f"sprints_by_project:{pid}", sid)
        # Reverse index file; harmless if absent.
        self._delete_file(f"user_stories_by_sprint:{sid}")
        return self._delete_file(f"sprint:{sid}")

    def hard_delete_user_story(self, story_id: UUID) -> bool:
        """Remove the user story row and drop it from every index that
        referenced it (per-project, per-sprint). Does NOT cascade to
        test cases -- the caller verifies the story has no non-rejected
        test cases before invoking this."""
        sid = str(story_id)
        existing = self.read(f"user_story:{sid}")
        if not existing:
            return False
        pid = str(existing.get("project_id") or "")
        sprint_id = existing.get("sprint_id")
        if pid:
            self._drop_from_index(f"user_stories_by_project:{pid}", sid)
        if sprint_id:
            self._drop_from_index(f"user_stories_by_sprint:{sprint_id}", sid)
        return self._delete_file(f"user_story:{sid}")

    def hard_delete_test_case(self, test_case_id: UUID) -> bool:
        """Remove the test case row and drop it from every index that
        referenced it (per-story, per-project, per-tag). On-disk Robot
        scripts owned by this TC are NOT removed by this method -- the
        router handles that so the storage layer doesn't need to know
        about the filesystem layout under settings.saved_projects_dir."""
        tid = str(test_case_id)
        existing = self.read(f"test_case:{tid}")
        if not existing:
            return False
        story_id = str(existing.get("user_story_id") or "")
        pid = str(existing.get("project_id") or "")
        tags = list(existing.get("tags") or [])
        if story_id:
            self._drop_from_index(f"test_cases_by_story:{story_id}", tid)
        if pid:
            self._drop_from_index(f"test_case_ids_project:{pid}", tid)
            for tag_name in tags:
                if not tag_name:
                    continue
                self._drop_from_index(f"test_case_ids_project_tag:{pid}:{tag_name}", tid)
        return self._delete_file(f"test_case:{tid}")

    def seed_static_tags(self, project_id: UUID) -> None:
        from uuid import NAMESPACE_URL, uuid5

        from ..models.tag import STATIC_TAGS, TagScope

        for name in STATIC_TAGS:
            key = f"tags:{project_id}:{name}"
            if self.read(key):
                continue
            tag_id = uuid5(NAMESPACE_URL, f"static-tag:{project_id}:{name}")
            tag = {
                "id": str(tag_id),
                "project_id": str(project_id),
                "name": name,
                "color": "#64748b",
                "scope": TagScope.static.value,
            }
            self.write(key, tag)
