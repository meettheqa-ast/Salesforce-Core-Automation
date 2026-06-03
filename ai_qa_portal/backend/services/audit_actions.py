"""Canonical audit action catalogue.

The IA audit flagged inconsistent naming in `log_action(.., action="...")`
calls scattered across routers -- a mix of ``story_*``, ``tc_*``,
``prompt_*``, ``admin_*``, and the outlier ``stories_bulk_deleted``.

This module documents the *canonical* dotted-naming convention
(``<entity>.<verb>``) and provides:

  * Constants for every action currently in use (legacy snake_case)
    so call sites can stop typing string literals.
  * Constants for the canonical dotted form so new code can adopt the
    convention.
  * ``LEGACY_TO_CANONICAL`` mapping so the upcoming activity feed
    endpoint (Phase 2) can present a unified view of historical
    + future events without a DB migration.

Migration policy: Phase 2 ships this catalogue + activity feed that
reads from it. Phase 3 then migrates call sites to emit the canonical
form (via the registry constants below) and the legacy strings are
retired after one release window.

NEW code should:
  - import the dotted constant (e.g. ``TC_ARCHIVED``)
  - pass the constant string to ``log_action``

Do NOT add new snake_case actions -- pick a dotted name and add it
under the appropriate section below.
"""

from __future__ import annotations

# ---------- canonical dotted action names ------------------------
#
# Format: <entity>.<verb>[.<qualifier>]
#
# Verbs are past-tense (the audit log records what happened, not
# what's about to happen). Qualifiers like ``.bulk`` are optional and
# only used when the bulk variant has distinct semantics from the
# single-item case.

# Test cases
TC_ARCHIVED = "tc.archived"
TC_PURGED = "tc.purged"
TC_RESTORED = "tc.restored"
TC_IMPORT_PARSED = "tc.import.parsed"
TC_IMPORT_COMMITTED = "tc.import.committed"
TC_IMPORT_ROLLED_BACK = "tc.import.rolled_back"

# Stories
STORY_CREATED = "story.created"
STORY_UPDATED = "story.updated"
STORY_ASSIGNED = "story.assigned"
STORY_COMMENT_ADDED = "story.comment_added"
STORY_CASES_GENERATED = "story.cases_generated"
STORY_SCRIPTS_BUILT = "story.scripts_built"
STORY_ARCHIVED = "story.archived"
STORY_DELETED = "story.deleted"
STORIES_BULK_DELETED = "story.bulk_deleted"

# Sprints
SPRINT_SCRIPTS_BUILT = "sprint.scripts_built"

# Runs
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
RUN_FAILED = "run.failed"  # new emitter (Phase 2)
RUN_PASSED = "run.passed"  # new emitter (Phase 2)

# Generation
GENERATION_COMPLETED = "generation.completed"  # new emitter (Phase 2)

# Prompts
PROMPT_TEMPLATE_CREATED = "prompt.template_created"
PROMPT_VERSION_ADDED = "prompt.version_added"
PROMPT_ACTIVATED = "prompt.activated"
PROMPT_ACTIVATED_BY_ADMIN = "prompt.activated_by_admin"  # new emitter (Phase 2)
PROMPT_RESET = "prompt.reset"
PROMPT_DELETED = "prompt.deleted"

# Healing
HEAL_AGGREGATED = "heal.aggregated"  # new emitter (Phase 2)

# Admin
ADMIN_PATCH_USER = "admin.user_patched"
ADMIN_REVOKE_SESSION = "admin.session_revoked"
ADMIN_TRANSFER_PROJECTS = "admin.projects_transferred"


# ---------- legacy -> canonical bridge ---------------------------
#
# Used by the activity feed to render legacy rows under their new
# names. Once the migration window passes (Phase 3) and we drop
# emitters of the legacy form, this mapping becomes read-only
# history.

LEGACY_TO_CANONICAL: dict[str, str] = {
    # Test cases
    "tc_archived": TC_ARCHIVED,
    "tc_purged": TC_PURGED,
    "tc_restored": TC_RESTORED,
    "tc_import_parsed": TC_IMPORT_PARSED,
    "tc_import_committed": TC_IMPORT_COMMITTED,
    "tc_import_rolled_back": TC_IMPORT_ROLLED_BACK,
    # Stories
    "story_created": STORY_CREATED,
    "story_updated": STORY_UPDATED,
    "story_assigned": STORY_ASSIGNED,
    "story_comment_added": STORY_COMMENT_ADDED,
    "story_cases_generated": STORY_CASES_GENERATED,
    "story_scripts_built": STORY_SCRIPTS_BUILT,
    "story_archived": STORY_ARCHIVED,
    "story_deleted": STORY_DELETED,
    "stories_bulk_deleted": STORIES_BULK_DELETED,
    # Sprints
    "sprint_scripts_built": SPRINT_SCRIPTS_BUILT,
    # Runs
    "run_started": RUN_STARTED,
    "run_finished": RUN_FINISHED,
    # Prompts
    "prompt_template_created": PROMPT_TEMPLATE_CREATED,
    "prompt_version_added": PROMPT_VERSION_ADDED,
    "prompt_activated": PROMPT_ACTIVATED,
    "prompt_reset": PROMPT_RESET,
    "prompt_deleted": PROMPT_DELETED,
    # Admin
    "admin_patch_user": ADMIN_PATCH_USER,
    "admin_revoke_session": ADMIN_REVOKE_SESSION,
    "admin_transfer_projects": ADMIN_TRANSFER_PROJECTS,
}


# ---------- human-readable labels for the activity feed ----------
#
# Maps the canonical action name to a short verb-phrase suitable
# for the activity timeline / notifications inbox.

HUMAN_LABELS: dict[str, str] = {
    TC_ARCHIVED: "archived a test case",
    TC_PURGED: "permanently deleted a test case",
    TC_RESTORED: "restored a test case",
    TC_IMPORT_PARSED: "uploaded a test case import",
    TC_IMPORT_COMMITTED: "imported test cases",
    TC_IMPORT_ROLLED_BACK: "rolled back a test case import",
    STORY_CREATED: "created a story",
    STORY_UPDATED: "updated a story",
    STORY_ASSIGNED: "assigned a story",
    STORY_COMMENT_ADDED: "commented on a story",
    STORY_CASES_GENERATED: "generated test cases for a story",
    STORY_SCRIPTS_BUILT: "built scripts for a story",
    STORY_ARCHIVED: "archived a story",
    STORY_DELETED: "deleted a story",
    STORIES_BULK_DELETED: "bulk-deleted stories",
    SPRINT_SCRIPTS_BUILT: "built scripts for a sprint",
    RUN_STARTED: "started a run",
    RUN_FINISHED: "finished a run",
    RUN_FAILED: "a run failed",
    RUN_PASSED: "a run passed",
    GENERATION_COMPLETED: "completed a generation",
    PROMPT_TEMPLATE_CREATED: "created a prompt template",
    PROMPT_VERSION_ADDED: "saved a new prompt version",
    PROMPT_ACTIVATED: "activated a prompt",
    PROMPT_ACTIVATED_BY_ADMIN: "set an org-default prompt",
    PROMPT_RESET: "reset a prompt override",
    PROMPT_DELETED: "deleted a prompt template",
    HEAL_AGGREGATED: "aggregated healing learnings",
    ADMIN_PATCH_USER: "updated a user",
    ADMIN_REVOKE_SESSION: "revoked a session",
    ADMIN_TRANSFER_PROJECTS: "transferred projects",
}


def canonical_name(action: str) -> str:
    """Return the canonical dotted name for an action. Legacy snake
    names are mapped via ``LEGACY_TO_CANONICAL``; canonical names
    pass through; unknown names pass through unchanged so the
    activity feed never drops a row just because the catalogue is
    out of date."""
    if action in LEGACY_TO_CANONICAL:
        return LEGACY_TO_CANONICAL[action]
    return action


def humanize(action: str) -> str:
    """Friendly verb-phrase for an action. Falls back to a generic
    rendering when the catalogue doesn't recognise the name."""
    canon = canonical_name(action)
    if canon in HUMAN_LABELS:
        return HUMAN_LABELS[canon]
    # Fallback: turn "thing.did_a_thing" into "did a thing on thing"
    if "." in canon:
        entity, verb = canon.split(".", 1)
        return f"{verb.replace('_', ' ')} ({entity})"
    return canon.replace("_", " ")
