"""Unified Salesforce metadata facade for runtime healing and planning."""

from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from ai_qa_portal.backend.services.salesforce_field_aliases import api_name_for_label, normalize_field_label

logger = logging.getLogger("ai_qa_portal.org_metadata")

_TTL_SECONDS = 600


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class OrgMetadataService:
    """Facade over app_schema + sf_dx_bridge + live describe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._describe_cache: dict[tuple[str, str], _CacheEntry] = {}
        self._validation_cache: dict[tuple[str, str], _CacheEntry] = {}

    def describe_object(
        self,
        object_name: str,
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> dict[str, Any]:
        cache_key = ((org_key or "").strip().lower(), (object_name or "").strip())
        now = time.monotonic()
        with self._lock:
            hit = self._describe_cache.get(cache_key)
            if hit and hit.expires_at > now:
                return hit.value

        value = self._describe_via_simple_salesforce(
            object_name=object_name,
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            security_token=security_token,
        )
        if not value:
            value = self._describe_via_sfdx(object_name)
        if not value:
            value = {"object": object_name, "fields": []}

        with self._lock:
            self._describe_cache[cache_key] = _CacheEntry(value=value, expires_at=now + _TTL_SECONDS)
        return value

    def picklist_values(
        self,
        object_name: str,
        field_api_name: str,
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> list[str]:
        desc = self.describe_object(
            object_name,
            org_key=org_key,
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            security_token=security_token,
        )
        wanted = (field_api_name or "").strip().lower()
        for field in desc.get("fields", []):
            if str(field.get("api_name") or "").strip().lower() == wanted:
                vals = field.get("picklist_values") or []
                return [str(v) for v in vals if str(v).strip()]
        return []

    def dependent_picklist_map(
        self,
        object_name: str,
        controlling_field: str,
        dependent_field: str,
        **_: Any,
    ) -> dict[str, list[str]]:
        # Phase 4 will populate this via full describe bitsets.
        return {}

    def validation_rules(
        self,
        object_name: str,
        *,
        org_key: str = "",
    ) -> list[dict[str, str]]:
        cache_key = ((org_key or "").strip().lower(), (object_name or "").strip())
        now = time.monotonic()
        with self._lock:
            hit = self._validation_cache.get(cache_key)
            if hit and hit.expires_at > now:
                return list(hit.value.get("rules") or [])

        rules: list[dict[str, str]] = []
        try:
            import sf_dx_bridge

            query = (
                "SELECT Id, ValidationName, ErrorDisplayField, ErrorMessage, "
                "ErrorConditionFormula, EntityDefinition.QualifiedApiName "
                "FROM ValidationRule "
                f"WHERE EntityDefinition.QualifiedApiName = '{object_name}'"
            )
            raw = sf_dx_bridge.run_soql_query(query)
            records = ((raw or {}).get("result") or {}).get("records") or []
            for row in records:
                rules.append(
                    {
                        "name": str(row.get("ValidationName") or ""),
                        "errorMessage": str(row.get("ErrorMessage") or ""),
                        "errorConditionFormula": str(row.get("ErrorConditionFormula") or ""),
                        "errorDisplayField": str(row.get("ErrorDisplayField") or ""),
                    }
                )
        except Exception:
            rules = []

        with self._lock:
            self._validation_cache[cache_key] = _CacheEntry(
                value={"rules": rules},
                expires_at=now + _TTL_SECONDS,
            )
        return rules

    def resolve_field_api_name(
        self,
        object_name: str,
        field_label: str,
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> str | None:
        if not field_label:
            return None
        alias = api_name_for_label(field_label)
        if alias:
            return alias
        label_norm = normalize_field_label(field_label)
        desc = self.describe_object(
            object_name,
            org_key=org_key,
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            security_token=security_token,
        )
        for field in desc.get("fields", []):
            label = normalize_field_label(str(field.get("label") or ""))
            if label == label_norm:
                return str(field.get("api_name") or "")
        return None

    def get_field(
        self,
        object_name: str,
        field_label: str,
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> dict[str, Any] | None:
        api_name = self.resolve_field_api_name(
            object_name,
            field_label,
            org_key=org_key,
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            security_token=security_token,
        )
        if not api_name:
            return None
        desc = self.describe_object(
            object_name,
            org_key=org_key,
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            security_token=security_token,
        )
        for field in desc.get("fields", []):
            if str(field.get("api_name") or "").lower() == api_name.lower():
                return field
        return None

    @staticmethod
    def safe_get_schema_context(
        prompt: str,
        sandbox_url: str,
        username: str,
        password: str,
        security_token: str = "",
    ) -> str:
        """Call app_schema.get_schema_context despite historical signature drift."""
        try:
            import app_schema

            fn = getattr(app_schema, "get_schema_context")
            params = list(inspect.signature(fn).parameters.keys())
            if len(params) >= 5:
                return str(fn(prompt, sandbox_url, username, password, security_token) or "")
            return str(fn(prompt, sandbox_url, username, password) or "")
        except Exception as exc:
            logger.debug("safe_get_schema_context failed: %s", exc)
            return ""

    @staticmethod
    def safe_get_object_schema_summary(
        object_name: str,
        sandbox_url: str,
        username: str,
        password: str,
        security_token: str = "",
    ) -> str:
        """Call app_schema.get_object_schema despite argument-order drift."""
        try:
            import app_schema

            fn = getattr(app_schema, "get_object_schema")
            params = list(inspect.signature(fn).parameters.keys())
            if params and params[0] == "object_name":
                return str(fn(object_name, sandbox_url, username, password, security_token) or "")
            return str(fn(sandbox_url, username, password, security_token, object_name) or "")
        except Exception as exc:
            logger.debug("safe_get_object_schema_summary failed: %s", exc)
            return ""

    def _describe_via_sfdx(self, object_name: str) -> dict[str, Any]:
        try:
            import sf_dx_bridge

            raw = sf_dx_bridge.describe_object_fields(object_name)
            records = ((raw or {}).get("result") or {}).get("records") or []
            fields: list[dict[str, Any]] = []
            for row in records:
                fields.append(
                    {
                        "api_name": str(row.get("QualifiedApiName") or ""),
                        "label": str(row.get("Label") or ""),
                        "type": str(row.get("DataType") or ""),
                        "required": bool(row.get("IsRequired")),
                        "picklist_values": [],
                    }
                )
            return {"object": object_name, "fields": fields}
        except Exception as exc:
            logger.debug("describe via sf_dx failed: %s", exc)
            return {}

    def _describe_via_simple_salesforce(
        self,
        *,
        object_name: str,
        sandbox_url: str,
        username: str,
        password: str,
        security_token: str,
    ) -> dict[str, Any]:
        if not sandbox_url or not username or not password:
            return {}
        try:
            from simple_salesforce import Salesforce

            domain = "test"
            sf = Salesforce(
                username=username.strip(),
                password=password.strip(),
                security_token=(security_token or "").strip(),
                domain=domain,
            )
            desc = getattr(sf, object_name).describe()
            fields: list[dict[str, Any]] = []
            for field in desc.get("fields", []) or []:
                picklist_values = [
                    str(v.get("label") or v.get("value") or "")
                    for v in (field.get("picklistValues") or [])
                    if v.get("active")
                ]
                fields.append(
                    {
                        "api_name": str(field.get("name") or ""),
                        "label": str(field.get("label") or ""),
                        "type": str(field.get("type") or ""),
                        "required": bool(
                            field.get("createable")
                            and (not field.get("nillable", True))
                            and (not field.get("defaultedOnCreate", False))
                        ),
                        "picklist_values": [v for v in picklist_values if v],
                        "reference_to": list(field.get("referenceTo") or []),
                    }
                )
            return {"object": object_name, "fields": fields, "fetched_at": str(date.today())}
        except Exception as exc:
            logger.debug("describe via simple-salesforce failed: %s", exc)
            return {}

