"""Classifier for Salesforce form-save errors."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, RawError

logger = logging.getLogger("ai_qa_portal.error_classifier")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PATTERNS_FILE = _ROOT / "_local_data" / "heal_patterns" / "en.yaml"

_TYPE_BY_KEY = {
    "required_field": ErrorType.REQUIRED_FIELD,
    "invalid_format": ErrorType.INVALID_FORMAT,
    "invalid_picklist": ErrorType.INVALID_PICKLIST,
    "validation_rule": ErrorType.VALIDATION_RULE,
    "conditional_required": ErrorType.CONDITIONAL_REQUIRED,
    "duplicate_value": ErrorType.DUPLICATE_VALUE,
    "unknown_error": ErrorType.UNKNOWN_ERROR,
}


class ErrorClassifier:
    def __init__(self, patterns_file: str | None = None, locale: str | None = None) -> None:
        selected_locale = (locale or os.environ.get("HEAL_PATTERN_LOCALE") or "en").strip().lower()
        default_file = _ROOT / "_local_data" / "heal_patterns" / f"{selected_locale}.yaml"
        self.patterns_file = Path(patterns_file) if patterns_file else default_file
        if not self.patterns_file.is_file():
            self.patterns_file = _DEFAULT_PATTERNS_FILE
        self._compiled = self._load_patterns()

    def classify(
        self,
        errors: list[RawError],
        *,
        sobject: str,
        metadata_service: Any,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> list[ClassifiedError]:
        out: list[ClassifiedError] = []
        for err in errors:
            error_type = self._classify_error_type(err)
            field_label = err.field_label or self._extract_field_label(err.raw_text)
            field_api_name = None
            if field_label:
                try:
                    field_api_name = metadata_service.resolve_field_api_name(
                        sobject,
                        field_label,
                        org_key=org_key,
                        sandbox_url=sandbox_url,
                        username=username,
                        password=password,
                        security_token=security_token,
                    )
                except Exception:
                    field_api_name = None
            detected_value = self._extract_detected_value(err.raw_text)
            confidence = self._confidence_for(error_type, err.raw_text, field_label)
            out.append(
                ClassifiedError(
                    raw=err,
                    error_type=error_type,
                    field_label=field_label,
                    field_api_name=field_api_name,
                    detected_value=detected_value,
                    confidence=confidence,
                )
            )
        return out

    def _classify_error_type(self, err: RawError) -> ErrorType:
        if err.source == "duplicate_modal":
            return ErrorType.DUPLICATE_VALUE
        text = err.raw_text or ""
        for key, regexes in self._compiled.items():
            et = _TYPE_BY_KEY.get(key)
            if et is None or et == ErrorType.UNKNOWN_ERROR:
                continue
            for rx in regexes:
                if rx.search(text):
                    return et
        return ErrorType.UNKNOWN_ERROR

    def _load_patterns(self) -> dict[str, list[re.Pattern[str]]]:
        rows: dict[str, list[str]] = {}
        if self.patterns_file.is_file():
            try:
                import yaml

                data = yaml.safe_load(self.patterns_file.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, list):
                            rows[str(key)] = [str(v) for v in value]
            except Exception as exc:
                logger.warning("Could not load heal pattern bank (%s): %s", self.patterns_file, exc)
        if not rows:
            rows = {
                "required_field": [r"(?i)required field", r"(?i)required fields? (are )?missing"],
                "invalid_format": [r"(?i)invalid", r"(?i)not a valid", r"(?i)format"],
                "invalid_picklist": [r"(?i)restricted picklist", r"(?i)not in (the )?list"],
                "validation_rule": [r"(?i)validation", r"(?i)error:"],
                "conditional_required": [r"(?i)is required when"],
                "duplicate_value": [r"(?i)duplicate", r"(?i)already exists"],
                "unknown_error": [r"(?i).*"],
            }
        compiled: dict[str, list[re.Pattern[str]]] = {}
        for key, patterns in rows.items():
            compiled[key] = []
            for pattern in patterns:
                try:
                    compiled[key].append(re.compile(pattern))
                except re.error:
                    logger.warning("Invalid regex in %s (%s): %s", self.patterns_file, key, pattern)
        return compiled

    @staticmethod
    def _extract_field_label(raw_text: str) -> str | None:
        if not raw_text:
            return None
        # Common Salesforce phrasing: "The Account Name field is required."
        m = re.search(r"(?i)\b([A-Za-z][A-Za-z0-9 /&_-]{1,60}) field\b", raw_text)
        if m:
            return m.group(1).strip()
        # "Industry is required when Type = Customer"
        m = re.search(r"(?i)^([A-Za-z][A-Za-z0-9 /&_-]{1,60}) is required when", raw_text)
        if m:
            return m.group(1).strip()
        # Quoted labels.
        m = re.search(r"['\"]([^'\"]{2,64})['\"]", raw_text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _extract_detected_value(raw_text: str) -> str | None:
        if not raw_text:
            return None
        # e.g. "value 'ABC' is not valid"
        m = re.search(r"(?i)value\s+['\"]([^'\"]+)['\"]", raw_text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _confidence_for(error_type: ErrorType, raw_text: str, field_label: str | None) -> float:
        if error_type == ErrorType.UNKNOWN_ERROR:
            return 0.4
        conf = 0.85
        if field_label:
            conf += 0.1
        if len(raw_text or "") < 12:
            conf -= 0.15
        return max(0.0, min(1.0, conf))

