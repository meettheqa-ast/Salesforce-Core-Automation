"""Runtime Salesforce Save/Create auto-healing engine."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ai_qa_portal.backend.services.error_classifier import ErrorClassifier
from ai_qa_portal.backend.services.heal_resolvers import StrategyRegistry
from ai_qa_portal.backend.services.heal_resolvers.base import ResolverContext
from ai_qa_portal.backend.services.heal_types import HealAttempt, HealResult, HealingDecision, RawError
from ai_qa_portal.backend.services.live_dom import LiveDOMService
from ai_qa_portal.backend.services.org_metadata import OrgMetadataService

logger = logging.getLogger("ai_qa_portal.form_healer")

_SENSITIVE_FIELD_RE = re.compile(r"(?i)(password|token|secret|ssn|credit)")


class RetryGovernor:
    """Budget and loop safety checks for one heal session."""

    _per_test_counter: dict[str, int] = {}

    def __init__(self, *, run_id: str = "") -> None:
        self.max_attempts_per_save = int(os.environ.get("HEAL_MAX_ATTEMPTS_PER_SAVE", "3"))
        self.max_per_test = int(os.environ.get("HEAL_MAX_PER_TEST", "6"))
        self.max_llm_calls = int(os.environ.get("HEAL_MAX_LLM_CALLS", "1"))
        self.timeout_s = int(os.environ.get("HEAL_TIMEOUT_S", "60"))
        self.started_at = time.monotonic()
        self.run_id = (run_id or "").strip() or "default"
        self.llm_calls = 0
        self._fingerprints: set[str] = set()
        self._last_error_signature: str | None = None
        self._same_error_repeats = 0

    def allow_attempt(self, attempt_n: int) -> tuple[bool, str]:
        if attempt_n > self.max_attempts_per_save:
            return False, "max_attempts_per_save"
        if self._run_event_count() >= self.max_per_test:
            return False, "max_per_test"
        if (time.monotonic() - self.started_at) > self.timeout_s:
            return False, "timeout"
        return True, ""

    def note_event(self) -> None:
        RetryGovernor._per_test_counter[self.run_id] = self._run_event_count() + 1

    def note_llm(self) -> bool:
        self.llm_calls += 1
        return self.llm_calls <= self.max_llm_calls

    def repeated_fingerprint(self, fingerprint: str) -> bool:
        if fingerprint in self._fingerprints:
            return True
        self._fingerprints.add(fingerprint)
        return False

    def same_error_again(self, signature: str) -> bool:
        if not signature:
            return False
        if self._last_error_signature == signature:
            self._same_error_repeats += 1
        else:
            self._same_error_repeats = 0
        self._last_error_signature = signature
        return self._same_error_repeats >= 1

    def _run_event_count(self) -> int:
        return int(RetryGovernor._per_test_counter.get(self.run_id, 0))


class HealingAudit:
    """Persists structured heal attempts to DB and optional file/event sinks."""

    def __init__(
        self,
        *,
        project_slug: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        event_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.project_slug = (project_slug or "").strip() or None
        self.run_id = (run_id or "").strip() or None
        self.job_id = (job_id or "").strip() or None
        self.event_cb = event_cb
        self._rows: list[dict[str, Any]] = []

    def emit(self, payload: dict[str, Any]) -> None:
        row = {"ts": datetime.now(UTC).isoformat(), **payload}
        self._rows.append(row)
        if self.event_cb is not None:
            self.event_cb(row)

    def persist_attempt(
        self,
        *,
        sobject: str,
        session_id: str,
        step_index: int | None,
        attempt: HealAttempt,
    ) -> None:
        self.emit(
            {
                "phase": "heal",
                "attempt": attempt.attempt_n,
                "sobject": sobject,
                "step_index": step_index,
                "errors": [_raw_error_json(e) for e in attempt.raw_errors],
                "decisions": [_decision_json(d) for d in attempt.decisions],
                "actions": list(attempt.actions_taken),
                "outcome": attempt.save_outcome,
                "latency_ms": attempt.latency_ms,
            }
        )
        try:
            from ai_qa_portal.backend.services.db import SessionLocal
            from ai_qa_portal.backend.services.db_models.heal import HealEvent, OrgFieldLearning

            with SessionLocal() as db:
                row = HealEvent(
                    project_slug=self.project_slug,
                    run_id=self.run_id,
                    generation_job_id=self.job_id,
                    sobject=sobject,
                    session_id=session_id,
                    step_index=step_index,
                    attempt_number=attempt.attempt_n,
                    error_type=(attempt.classified[0].error_type.value if attempt.classified else ""),
                    field_label=(attempt.classified[0].field_label if attempt.classified else None),
                    strategy=(attempt.decisions[0].strategy if attempt.decisions else ""),
                    outcome=attempt.save_outcome,
                    latency_ms=attempt.latency_ms,
                    payload=_attempt_json(attempt),
                )
                db.add(row)
                self._upsert_learning(db, sobject, attempt)
                db.commit()
        except Exception as exc:
            logger.debug("HealingAudit.persist_attempt failed: %s", exc)

    def write_file(self) -> None:
        if not self.run_id:
            return
        path = Path("Results") / self.run_id / "heal_audit.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._rows, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("HealingAudit.write_file failed: %s", exc)

    def _upsert_learning(self, db, sobject: str, attempt: HealAttempt) -> None:
        if not attempt.classified or not attempt.decisions:
            return
        err = attempt.classified[0]
        decision = attempt.decisions[0]
        if not err.field_api_name:
            return
        from ai_qa_portal.backend.services.db_models.heal import OrgFieldLearning

        row = (
            db.query(OrgFieldLearning)
            .filter(OrgFieldLearning.project_slug == (self.project_slug or ""))
            .filter(OrgFieldLearning.sobject == sobject)
            .filter(OrgFieldLearning.field_api_name == err.field_api_name)
            .filter(OrgFieldLearning.error_type == err.error_type.value)
            .one_or_none()
        )
        success = attempt.save_outcome == "passed"
        if row is None:
            row = OrgFieldLearning(
                project_slug=(self.project_slug or ""),
                sobject=sobject,
                field_api_name=err.field_api_name,
                error_type=err.error_type.value,
                total_count=1,
                success_count=(1 if success else 0),
                failure_count=(0 if success else 1),
                last_success_strategy=(decision.strategy if success else None),
                last_failed_strategy=(None if success else decision.strategy),
            )
            db.add(row)
            return
        row.total_count += 1
        if success:
            row.success_count += 1
            row.last_success_strategy = decision.strategy
        else:
            row.failure_count += 1
            row.last_failed_strategy = decision.strategy
        row.updated_at = datetime.now(UTC)
        db.add(row)


class FieldFillExecutor:
    """Applies healing decisions in the active RF-MCP browser session."""

    def apply(self, session_id: str, decisions: list[HealingDecision]) -> list[str]:
        import mcp_bridge

        actions: list[str] = []
        for decision in decisions:
            label = decision.target_field_label or ""
            strategy = decision.strategy
            try:
                if strategy == "select_picklist":
                    mcp_bridge.execute_step(session_id, "Open Dropdown With Fallback", [label])
                    if decision.fill_value:
                        mcp_bridge.execute_step(
                            session_id,
                            "Select Dropdown Option",
                            [label, decision.fill_value],
                        )
                    else:
                        mcp_bridge.execute_step(session_id, "Select Random Valid Picklist Option", [])
                    actions.append(f"picklist:{label}")
                elif strategy in ("fill_text", "regenerate_duplicate"):
                    value = decision.fill_value or ""
                    mcp_bridge.execute_step(session_id, "Enter Text With Fallback", [label, value])
                    actions.append(f"text:{label}")
                elif strategy == "set_lookup":
                    value = decision.fill_value or "AutoLookup"
                    mcp_bridge.execute_step(session_id, "Set Lookup Field", [label, value])
                    actions.append(f"lookup:{label}")
                elif strategy == "abort_save":
                    actions.append(f"abort:{label}")
                else:
                    actions.append(f"skip:{label}")
            except Exception as exc:
                actions.append(f"error:{label}:{type(exc).__name__}")
                logger.debug("field fill failed (%s/%s): %s", strategy, label, exc)
        return actions


class FormHealer:
    def __init__(self) -> None:
        self.metadata = OrgMetadataService()
        self.live_dom = LiveDOMService()
        self.classifier = ErrorClassifier()
        self.registry = StrategyRegistry()
        self.filler = FieldFillExecutor()

    def heal_save(
        self,
        *,
        session_id: str,
        sobject: str,
        save_action: str = "Save",
        project_slug: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        step_index: int | None = None,
        duplicate_strategy: str = "regenerate",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
        event_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> HealResult:
        if not session_id:
            return HealResult(outcome="aborted", reason="missing_session_id")
        run_key = (run_id or job_id or f"{project_slug or 'default'}")
        governor = RetryGovernor(run_id=run_key)
        audit = HealingAudit(
            project_slug=project_slug,
            run_id=run_id,
            job_id=job_id,
            event_cb=event_cb,
        )
        attempts: list[HealAttempt] = []
        healed_fields: set[str] = set()

        self._click_save(session_id, save_action)
        attempt_n = 0
        while True:
            attempt_n += 1
            allowed, reason = governor.allow_attempt(attempt_n)
            if not allowed:
                audit.write_file()
                return HealResult(
                    outcome="budget_exhausted",
                    reason=reason,
                    attempts=attempts,
                    healed_fields=sorted(healed_fields),
                )

            t0 = time.monotonic()
            self._wait_for_settle(session_id)
            raw_errors = self.live_dom.scrape_errors(session_id)
            if not raw_errors:
                done = HealAttempt(
                    attempt_n=attempt_n,
                    started_at=datetime.now(UTC),
                    save_outcome="passed",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                attempts.append(done)
                audit.persist_attempt(
                    sobject=sobject,
                    session_id=session_id,
                    step_index=step_index,
                    attempt=done,
                )
                audit.write_file()
                return HealResult(
                    outcome="passed",
                    attempts=attempts,
                    healed_fields=sorted(healed_fields),
                )

            signature = _error_signature(raw_errors)
            if governor.same_error_again(signature):
                aborted = HealAttempt(
                    attempt_n=attempt_n,
                    started_at=datetime.now(UTC),
                    raw_errors=list(raw_errors),
                    save_outcome="failed_same_errors",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                attempts.append(aborted)
                audit.persist_attempt(
                    sobject=sobject,
                    session_id=session_id,
                    step_index=step_index,
                    attempt=aborted,
                )
                audit.write_file()
                return HealResult(
                    outcome="aborted",
                    reason="same_error_repeat",
                    attempts=attempts,
                    healed_fields=sorted(healed_fields),
                )

            classified = self.classifier.classify(
                list(raw_errors),
                sobject=sobject,
                metadata_service=self.metadata,
                org_key=(project_slug or ""),
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                security_token=security_token,
            )
            ctx = ResolverContext(
                session_id=session_id,
                sobject=sobject,
                org_key=(project_slug or ""),
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                security_token=security_token,
                duplicate_strategy=duplicate_strategy,
                metadata=self.metadata,
                live_dom=self.live_dom,
                llm_budget_cb=governor.note_llm,
            )
            decisions = self.registry.resolve_each(classified, ctx)
            for dec in decisions:
                fingerprint = _decision_fingerprint(dec)
                if governor.repeated_fingerprint(fingerprint):
                    dec_notes = f"{dec.notes} (duplicate decision fingerprint)"
                    decisions = [
                        HealingDecision(
                            strategy="abort_save",
                            target_field_label=dec.target_field_label,
                            fill_value=None,
                            requires_llm=dec.requires_llm,
                            notes=dec_notes,
                            confidence=dec.confidence,
                        )
                    ]
                    break

            actions = self.filler.apply(session_id, decisions)
            for d in decisions:
                if d.target_field_label:
                    healed_fields.add(d.target_field_label)
            attempt = HealAttempt(
                attempt_n=attempt_n,
                started_at=datetime.now(UTC),
                raw_errors=list(raw_errors),
                classified=list(classified),
                decisions=list(decisions),
                actions_taken=list(actions),
                save_outcome="failed_with_new_errors",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            attempts.append(attempt)
            governor.note_event()
            audit.persist_attempt(
                sobject=sobject,
                session_id=session_id,
                step_index=step_index,
                attempt=attempt,
            )

            if not actions or all(a.startswith("abort:") or a.startswith("skip:") for a in actions):
                attempt.save_outcome = "aborted"
                audit.write_file()
                return HealResult(
                    outcome="aborted",
                    reason="no_actionable_fix",
                    attempts=attempts,
                    healed_fields=sorted(healed_fields),
                )

            self._click_save(session_id, save_action)

    @staticmethod
    def _wait_for_settle(session_id: str) -> None:
        import mcp_bridge

        try:
            mcp_bridge.execute_step(session_id, "Wait For Lightning Spinners Absent", ["timeout=8s"])
        except Exception:
            pass
        try:
            mcp_bridge.execute_step(session_id, "Sleep", ["0.5s"])
        except Exception:
            pass

    @staticmethod
    def _click_save(session_id: str, save_action: str) -> None:
        import mcp_bridge

        mcp_bridge.execute_step(session_id, "Select Dialog Button", [save_action])


def _error_signature(errors: list[RawError]) -> str:
    parts = [f"{e.source}|{e.field_label or ''}|{e.raw_text[:120]}" for e in errors]
    return hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()


def _decision_fingerprint(decision: HealingDecision) -> str:
    raw = f"{decision.strategy}|{decision.target_field_label or ''}|{decision.fill_value or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _raw_error_json(err: RawError) -> dict[str, Any]:
    return asdict(err)


def _decision_json(decision: HealingDecision) -> dict[str, Any]:
    out = asdict(decision)
    if decision.target_field_label and _SENSITIVE_FIELD_RE.search(decision.target_field_label):
        out["fill_value"] = "***"
    return out


def _attempt_json(attempt: HealAttempt) -> dict[str, Any]:
    out = asdict(attempt)
    for decision in out.get("decisions", []):
        label = str(decision.get("target_field_label") or "")
        if label and _SENSITIVE_FIELD_RE.search(label):
            decision["fill_value"] = "***"
    return out

