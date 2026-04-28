"""ExternalIssueProvider protocol.

The local hierarchy (Sprint -> UserStory -> TestCase) is the source of
truth for THIS app. Providers translate between the local models and a
backing system (Jira, Azure DevOps, etc.).

Sync direction is intentionally not encoded in the protocol -- a
specific provider can be:

  * pull-only  (read external, write local; never modifies external)
  * push-only  (e.g. Xray test-result reporting; reads local, writes external)
  * two-way    (a real Jira integration probably ends up here)

The model fields that pair with this protocol live on every entity:
`external_id`, `external_source`, `external_url`, `last_synced_at`,
`external_payload`. A provider's job is to populate those on the local
rows it imports, and (if push-capable) to mirror local changes back.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExternalIssueProvider(Protocol):
    """Every concrete provider implements all six methods. Some can
    legitimately raise NotImplementedError if their system doesn't
    support that direction (e.g. a read-only provider for push_*).
    Callers should be defensive."""

    source_name: str  # canonical identifier: "jira", "azure-devops", ...

    # --- READ side: external -> local ---------------------------------

    def fetch_sprints(self, project_external_id: str) -> list[dict[str, Any]]:
        """Return a list of sprint dicts shaped to seed `models.sprint.Sprint`
        rows. The caller will set `external_source` on each before save."""
        ...

    def fetch_stories_for_sprint(self, sprint_external_id: str) -> list[dict[str, Any]]:
        """Same shape as `models.user_story.UserStory.model_dump()` for
        sprint-scoped user stories."""
        ...

    def fetch_test_cases_for_story(self, story_external_id: str) -> list[dict[str, Any]]:
        """Same shape as `models.test_case.TestCase.model_dump()`."""
        ...

    # --- WRITE side: local -> external --------------------------------

    def push_test_case_result(
        self,
        test_case_external_id: str,
        status: str,           # "PASS" | "FAIL" | "SKIP"
        run_url: str,          # link back to our Results/<run_folder>
    ) -> None:
        """Report the latest run outcome of a test case back to the
        source system (e.g. attach an Xray Test Execution comment)."""
        ...

    def push_story_status(
        self,
        story_external_id: str,
        status: str,
    ) -> None:
        """Reflect local story status changes back to the issue tracker
        if the integration is two-way."""
        ...
