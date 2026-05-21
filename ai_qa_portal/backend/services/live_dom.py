"""Live Salesforce DOM scraping helpers for form-healing."""

from __future__ import annotations

import json
import logging
from typing import Any

from ai_qa_portal.backend.services.heal_types import RawError

logger = logging.getLogger("ai_qa_portal.live_dom")


_SCRAPE_ERRORS_JS = r"""
(() => {
  const text = (el) => (el && (el.innerText || el.textContent || "") || "").trim();
  const toArray = (nodes) => Array.from(nodes || []);
  const nearestLabel = (el) => {
    if (!el) return null;
    const field = el.closest(".slds-form-element, .uiInput, .forcePageBlockItem");
    if (!field) return null;
    const lbl = field.querySelector("label, .slds-form-element__label, .labelCol");
    const val = text(lbl);
    return val || null;
  };
  const out = {
    modalSnag: [],
    inline: [],
    banners: [],
    toasts: [],
    duplicateModal: null,
  };

  toArray(document.querySelectorAll("ul.errorsList a, ul.errorsList li a")).forEach((a) => {
    const t = text(a);
    if (t) out.modalSnag.push({ text: t, fieldLabel: t, locator: "ul.errorsList a" });
  });

  toArray(document.querySelectorAll(".slds-form-element__help, .errorsList li, .field-level-help")).forEach((node) => {
    const t = text(node);
    if (!t) return;
    out.inline.push({
      text: t,
      fieldLabel: nearestLabel(node),
      locator: ".slds-form-element__help/.errorsList li",
    });
  });

  toArray(document.querySelectorAll(".slds-notify_alert.slds-theme_error, .slds-theme_error, .errorMsg")).forEach((node) => {
    const t = text(node);
    if (t) out.banners.push({ text: t, fieldLabel: null, locator: ".slds-theme_error/.errorMsg" });
  });

  toArray(document.querySelectorAll("[data-key='error'], .toastMessage, .slds-notify_toast.slds-theme_error")).forEach((node) => {
    const t = text(node);
    if (t) out.toasts.push({ text: t, fieldLabel: null, locator: "[data-key=error]/toastMessage" });
  });

  const duplicateNode = document.querySelector(
    "[data-aura-class*='ForceDuplicateAlert'], .forceDuplicateRecordSet, .duplicateRecordAlert, .modal-container .slds-modal__header"
  );
  const duplicateText = text(duplicateNode);
  if (duplicateNode && /duplicate/i.test(duplicateText || "")) {
    out.duplicateModal = {
      text: duplicateText || "Duplicate detected",
      fieldLabel: null,
      locator: "[data-aura-class*=ForceDuplicateAlert]",
    };
  }

  return JSON.stringify(out);
})()
"""


_ENUM_PICKLIST_OPTIONS_JS = r"""
((label) => {
  const norm = (v) => (v || "").toLowerCase().replace(/\s+/g, " ").trim();
  const wanted = norm(label);
  const wrappers = Array.from(document.querySelectorAll(".slds-form-element, .forcePageBlockItem, .uiInput"));
  let target = null;
  for (const w of wrappers) {
    const l = w.querySelector("label, .slds-form-element__label");
    if (norm((l && (l.innerText || l.textContent)) || "") === wanted) {
      target = w;
      break;
    }
  }
  const vals = [];
  if (target) {
    const opts = target.querySelectorAll("[role='option'], .slds-listbox__option, option");
    for (const opt of Array.from(opts)) {
      const t = ((opt.innerText || opt.textContent || "") + "").trim();
      if (t && t.toLowerCase() !== "none") vals.push(t);
    }
  }
  return JSON.stringify(vals);
})(arguments[0])
"""


class LiveDOMService:
    def scrape_errors(self, session_id: str) -> list[RawError]:
        import mcp_bridge

        payload = mcp_bridge.execute_step(session_id, "Execute Javascript", [_SCRAPE_ERRORS_JS])
        data = self._extract_json_payload(payload, default={})
        rows: list[RawError] = []

        for item in data.get("modalSnag", []) or []:
            rows.append(
                RawError(
                    source="modal_snag",
                    raw_text=str(item.get("text") or ""),
                    field_label=item.get("fieldLabel"),
                    severity="error",
                    locator_used=str(item.get("locator") or "ul.errorsList a"),
                )
            )
        for item in data.get("inline", []) or []:
            rows.append(
                RawError(
                    source="inline_required",
                    raw_text=str(item.get("text") or ""),
                    field_label=item.get("fieldLabel"),
                    severity="error",
                    locator_used=str(item.get("locator") or ".slds-form-element__help"),
                )
            )
        for item in data.get("banners", []) or []:
            rows.append(
                RawError(
                    source="page_banner",
                    raw_text=str(item.get("text") or ""),
                    field_label=item.get("fieldLabel"),
                    severity="error",
                    locator_used=str(item.get("locator") or ".slds-theme_error"),
                )
            )
        for item in data.get("toasts", []) or []:
            rows.append(
                RawError(
                    source="error_toast",
                    raw_text=str(item.get("text") or ""),
                    field_label=item.get("fieldLabel"),
                    severity="error",
                    locator_used=str(item.get("locator") or "[data-key='error']"),
                )
            )

        duplicate = data.get("duplicateModal")
        if isinstance(duplicate, dict):
            rows.append(
                RawError(
                    source="duplicate_modal",
                    raw_text=str(duplicate.get("text") or "Duplicate detected"),
                    field_label=duplicate.get("fieldLabel"),
                    severity="error",
                    locator_used=str(duplicate.get("locator") or "[data-aura-class*=ForceDuplicateAlert]"),
                )
            )
        return [r for r in rows if r.raw_text.strip()]

    def enumerate_picklist_options(self, session_id: str, field_label: str) -> list[str]:
        import mcp_bridge

        payload = mcp_bridge.execute_step(
            session_id,
            "Execute Javascript",
            [_ENUM_PICKLIST_OPTIONS_JS, "ARGUMENTS", field_label],
        )
        data = self._extract_json_payload(payload, default=[])
        if isinstance(data, list):
            return [str(v) for v in data if str(v).strip()]
        return []

    @staticmethod
    def _extract_json_payload(payload: Any, *, default: Any) -> Any:
        if isinstance(payload, (dict, list)):
            candidate_values: list[Any] = []
            if isinstance(payload, dict):
                candidate_values.extend(
                    [
                        payload.get("return_value"),
                        payload.get("output"),
                        payload.get("value"),
                        payload.get("result"),
                        payload.get("raw"),
                    ]
                )
                # Some RF-MCP payloads put the return value under nested keys.
                nested = payload.get("data")
                if isinstance(nested, dict):
                    candidate_values.extend([nested.get("return_value"), nested.get("output"), nested.get("value")])
            else:
                candidate_values.append(payload)
            for cand in candidate_values:
                if cand is None:
                    continue
                if isinstance(cand, (dict, list)):
                    return cand
                text = str(cand).strip()
                if not text:
                    continue
                try:
                    return json.loads(text)
                except Exception:
                    continue
        try:
            return json.loads(str(payload))
        except Exception:
            logger.debug("live_dom json parse failed for payload type %s", type(payload).__name__)
            return default

