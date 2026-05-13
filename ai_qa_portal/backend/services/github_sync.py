"""Portal -> GitHub orchestration.

Two flows live here:

* :func:`push_project_suites`  -- walk every approved test case in a
  project, materialise the ``.robot`` content (using the existing
  :class:`TestCaseScriptBuilder`), and push the files into the
  connected repo under the configured ``suites_root_path``.

* :func:`refresh_workflow_yaml` -- render the portal-owned workflow
  YAML from the Jinja template + the project's current
  ``runner='github_actions'`` schedules, and PUT it into the repo.

The provider is built lazily from a :class:`GitHubConnection` row; the
Fernet-encrypted credential payload is decrypted here and never returned
to callers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.credential_service import CredentialService
from ai_qa_portal.backend.services.db_models.github import (
    GitHubAuthKind,
    GitHubConnection,
    GitHubRepo,
)
from ai_qa_portal.backend.services.db_models.schedules import Schedule, ScheduleRunner
from ai_qa_portal.backend.services.integrations.github import GitHubProvider

logger = logging.getLogger("ai_qa_portal.github_sync")

_TEMPLATE_DIR = Path(__file__).parent / "github_templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["yml", "yaml"]),
    keep_trailing_newline=True,
)


# ---- provider construction ------------------------------------------------

def build_provider(connection: GitHubConnection) -> GitHubProvider:
    """Decrypt the stored credentials and return a configured provider."""
    svc = CredentialService(settings.fernet_key or None)
    if connection.auth_kind == GitHubAuthKind.app.value:
        if not connection.encrypted_private_key:
            raise ValueError("GitHub connection is App-typed but has no private key stored")
        pem = svc.decrypt(connection.encrypted_private_key)
        return GitHubProvider(
            app_id=connection.app_id,
            installation_id=connection.installation_id,
            private_key_pem=pem,
            owner_login=connection.owner_login,
        )
    if not connection.encrypted_access_token:
        raise ValueError("GitHub connection is PAT-typed but has no token stored")
    token = svc.decrypt(connection.encrypted_access_token)
    return GitHubProvider(access_token=token, owner_login=connection.owner_login)


# ---- workflow YAML --------------------------------------------------------

def render_workflow_yaml(*, schedules: Iterable[Schedule]) -> str:
    """Render the workflow YAML with each ``github_actions`` schedule
    materialised as a ``schedule:`` cron block. Schedules with
    ``enabled=False`` are excluded."""
    relevant = [s for s in schedules if s.runner == ScheduleRunner.github_actions.value and s.enabled]
    template = _jinja.get_template("portal_automation.yml.j2")
    return template.render(schedules=relevant)


def refresh_workflow_yaml(
    db: Session,
    *,
    repo: GitHubRepo,
    connection: GitHubConnection,
) -> str:
    """Push a fresh workflow YAML to ``repo`` covering all of the
    project's enabled GitHub-Actions schedules. Returns the rendered
    content (so the router can echo it back / diff it on the UI)."""
    schedules = (
        db.query(Schedule)
        .filter(
            Schedule.project_slug == repo.project_slug,
            Schedule.runner == ScheduleRunner.github_actions.value,
        )
        .all()
    )
    yaml_content = render_workflow_yaml(schedules=schedules)
    provider = build_provider(connection)
    with provider:
        provider.ensure_workflow_yaml(
            owner=repo.owner,
            repo=repo.name,
            branch=repo.default_branch,
            workflow_path=repo.workflow_path,
            yaml_content=yaml_content,
            commit_message="chore(portal): refresh automation workflow",
        )
    repo.last_pushed_at = datetime.now(UTC)
    db.commit()
    return yaml_content


# ---- script push ----------------------------------------------------------

def push_project_suites(
    db: Session,
    *,
    repo: GitHubRepo,
    connection: GitHubConnection,
    project_id,
    persona=None,
    org=None,
    skip_dryrun: bool = True,
) -> dict[str, int | list[str]]:
    """Walk every approved test case for ``project_id``, compile each to a
    ``.robot`` suite, and push them under ``repo.suites_root_path`` in a
    single commit-per-file pass.

    Filenames are ``{repo.suites_root_path}/story_{story_id}/case_{case_id}.robot``
    so the workflow's ``suite_path`` input can be a stable, predictable
    path the portal hands to ``workflow_dispatch``.
    """
    from ai_qa_portal.backend.services.test_case_script_builder import TestCaseScriptBuilder
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    store = JsonFileBackend(settings.data_dir)
    builder = TestCaseScriptBuilder()

    stories = store.list_user_stories(project_id)
    files: list[dict[str, str]] = []
    pushed_paths: list[str] = []

    for story in stories:
        story_id = str(story["id"])
        for tc_row in store.get_test_cases_by_story(story["id"]):
            from ai_qa_portal.backend.models.test_case import TestCase
            tc = TestCase.model_validate(tc_row)
            if tc.status != "approved":
                continue
            try:
                robot_source = builder.build_robot_script(
                    tc, persona=persona, org=org, skip_dryrun=skip_dryrun,
                    db=db, project_slug=repo.project_slug,
                )
            except Exception as exc:  # noqa: BLE001 -- one bad case shouldn't fail the bulk push
                logger.warning("Skipping test case %s during push (build failed): %s", tc.id, exc)
                continue
            path = f"{repo.suites_root_path.rstrip('/')}/story_{story_id[:12]}/case_{tc.id.hex[:12]}.robot"
            files.append({"path": path, "content": robot_source})
            pushed_paths.append(path)

    if not files:
        return {"committed": 0, "files": []}

    provider = build_provider(connection)
    with provider:
        provider.push_files(
            owner=repo.owner,
            repo=repo.name,
            branch=repo.default_branch,
            files=files,
            commit_message="chore(portal): sync approved test suites",
        )
    repo.last_pushed_at = datetime.now(UTC)
    db.commit()
    return {"committed": len(files), "files": pushed_paths}
