/**
 * Legacy `/sfdx` route. The page has moved to `/org-inspector`
 * (rename happened in the Dev Tools IA refactor). This shim
 * preserves every bookmark, deep link, and stored Admin tile that
 * still points at `/sfdx`. Keep until callers have been audited.
 *
 * See `safe_dev_tools_refactor` Phase 3 for the rename rationale.
 */

import { redirect } from "next/navigation";

export default function SfdxLegacyRedirect() {
  redirect("/org-inspector");
}
