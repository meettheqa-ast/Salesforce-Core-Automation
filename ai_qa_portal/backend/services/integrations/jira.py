"""Jira provider stub.

This file is INTENTIONALLY UNIMPLEMENTED. It exists so a future Jira
integration PR knows exactly where to land:

  1. Implement each method using `atlassian-python-api` (or raw REST).
  2. Register the provider in `registry.py`:
        from .jira import JiraProvider
        _PROVIDERS["jira"] = JiraProvider(base_url=..., auth=...)
  3. Add `POST /sprints/import?source=jira&project_id=...` in a new
     router, calling `provider.fetch_sprints(...)` and persisting each
     result via `_store.save_sprint(...)` with `external_source="jira"`.

No model changes, no router restructuring, no UI rewiring -- the entity
fields (`external_id`, `external_source`, etc.) and the
`ExternalIssueProvider` protocol are already in place.
"""

from __future__ import annotations

from typing import Any


class JiraProvider:
    """Future Jira REST + (optional Xray) integration. Every method
    raises NotImplementedError until the real PR lands.

    Constructor signature is left open: the real provider will likely
    take `base_url`, `email`, `api_token`, and optionally an Xray
    client. We deliberately don't pin the signature here so future
    implementers aren't forced to keep a useless backwards-compat
    shape -- nobody constructs this class today."""

    source_name: str = "jira"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: D401
        raise NotImplementedError(
            "JiraProvider is a placeholder. See "
            "ai_qa_portal/backend/services/integrations/jira.py for the "
            "implementation contract."
        )

    def fetch_sprints(self, project_external_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "JiraProvider.fetch_sprints is not implemented yet."
        )

    def fetch_stories_for_sprint(self, sprint_external_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "JiraProvider.fetch_stories_for_sprint is not implemented yet."
        )

    def fetch_test_cases_for_story(self, story_external_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "JiraProvider.fetch_test_cases_for_story is not implemented yet."
        )

    def push_test_case_result(
        self,
        test_case_external_id: str,
        status: str,
        run_url: str,
    ) -> None:
        raise NotImplementedError(
            "JiraProvider.push_test_case_result is not implemented yet."
        )

    def push_story_status(self, story_external_id: str, status: str) -> None:
        raise NotImplementedError(
            "JiraProvider.push_story_status is not implemented yet."
        )
