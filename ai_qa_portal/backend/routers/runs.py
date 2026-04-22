from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ai_qa_portal.backend.config import REPO_ROOT, settings
from ..models.org import SalesforceOrg
from ..models.persona import RunRequest, RunResponse
from ..models.test_case import TestCase, TestCaseStatus
from ..models.user_story import UserStory
from ..routers.personas import load_all_personas
from ..services.credential_service import CredentialService
from ..services.persona_resolver import PersonaResolver
from ..services.script_runner import ScriptRunner
from ..services.test_case_script_builder import TestCaseScriptBuilder
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(prefix="/run", tags=["runs"])
_resolver = PersonaResolver()
_runner = ScriptRunner(settings.output_dir)
_store = JsonFileBackend(settings.data_dir)
_script_builder = TestCaseScriptBuilder()


def _get_org(org_id):
    orgs = _store.read("orgs").get("items", [])
    return next((o for o in orgs if str(o["id"]) == str(org_id)), None)


@router.post("", response_model=RunResponse)
async def trigger_run(body: RunRequest, background_tasks: BackgroundTasks):
    all_personas = load_all_personas()

    try:
        persona, method = _resolver.resolve(
            project_id=body.project_id,
            org_id=body.org_id,
            prompt=body.prompt,
            ui_persona_id=body.persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    org = _get_org(body.org_id)
    if not org:
        raise HTTPException(404, f"Org {body.org_id} not found")

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    run_id = uuid4()

    background_tasks.add_task(
        _execute_in_background,
        run_id=run_id,
        login_url=org["login_url"],
        username=persona.username,
        password=password,
        prompt=body.prompt,
    )

    return RunResponse(
        run_id=run_id,
        resolved_persona=persona.name,
        resolution_method=method,
        status="started",
        log_url=f"/outputs/run_{run_id.hex[:8]}",
    )


class RunUserStoryBody(BaseModel):
    org_id: UUID
    persona_id: Optional[UUID] = None


class RunTagBody(BaseModel):
    project_id: UUID
    org_id: UUID
    persona_id: Optional[UUID] = None


@router.post("/user-story/{story_id}", response_model=list[RunResponse])
def run_tests_for_user_story(story_id: UUID, body: RunUserStoryBody):
    sid = story_id
    org_id = body.org_id
    persona_id = body.persona_id

    try:
        row = _store.get_user_story(sid)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = UserStory.model_validate(row)
    project_id = story.project_id

    tcs_raw = _store.get_test_cases_by_story(sid)
    tcs = [TestCase.model_validate(r) for r in tcs_raw]
    tcs = [t for t in tcs if t.status == TestCaseStatus.approved and not t.stale]
    if not tcs:
        raise HTTPException(400, "No approved non-stale test cases for this story")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_all_personas()
    prompt = f"Execute automated tests for user story: {story.title}"
    try:
        persona, method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    out: list[RunResponse] = []
    gen_dir = REPO_ROOT / "Tests" / "Generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for tc in tcs:
        robot = _script_builder.build_robot_script(tc, persona, org_model)
        path = gen_dir / f"story_{tc.id.hex[:12]}.robot"
        path.write_text(robot, encoding="utf-8")
        run_id, _log = _runner.run(
            str(path.resolve()),
            persona.username,
            password,
            org_model.login_url,
        )
        out.append(
            RunResponse(
                run_id=run_id,
                resolved_persona=persona.name,
                resolution_method=method,
                status="started",
                log_url=f"/outputs/run_{run_id.hex[:8]}",
            )
        )
    return out


@router.post("/tag/{tag_name}", response_model=list[RunResponse])
def run_tests_for_tag(tag_name: str, body: RunTagBody):
    project_id = body.project_id
    org_id = body.org_id
    persona_id = body.persona_id

    tcs_raw = _store.get_test_cases_by_tag(project_id, tag_name)
    tcs = [TestCase.model_validate(r) for r in tcs_raw if not r.get("stale")]
    if not tcs:
        raise HTTPException(400, f"No approved non-stale test cases tagged {tag_name!r}")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_all_personas()
    prompt = f"Run tests tagged {tag_name}"
    try:
        persona, method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    out: list[RunResponse] = []
    gen_dir = REPO_ROOT / "Tests" / "Generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for tc in tcs:
        robot = _script_builder.build_robot_script(tc, persona, org_model)
        path = gen_dir / f"story_{tc.id.hex[:12]}.robot"
        path.write_text(robot, encoding="utf-8")
        run_id, _log = _runner.run(
            str(path.resolve()),
            persona.username,
            password,
            org_model.login_url,
        )
        out.append(
            RunResponse(
                run_id=run_id,
                resolved_persona=persona.name,
                resolution_method=method,
                status="started",
                log_url=f"/outputs/run_{run_id.hex[:8]}",
            )
        )
    return out


def _execute_in_background(
    run_id,
    login_url: str,
    username: str,
    password: str,
    prompt: str,
) -> None:
    """Background task: generate .robot from prompt then run it."""
    import sys
    import os
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    sys.path.insert(0, repo_root)

    try:
        from ai_bridge import generate_test_from_prompt
        output_path = generate_test_from_prompt(prompt)
        _runner.run(
            str(output_path),
            username=username,
            password=password,
            login_url=login_url,
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Background run %s failed: %s", run_id, exc)
