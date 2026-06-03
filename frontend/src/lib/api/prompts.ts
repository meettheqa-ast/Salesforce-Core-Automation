/**
 * Focused client module for the AI prompt registry.
 *
 * Re-exports the same shapes that already live in `lib/api.ts` so
 * callers can choose either path:
 *
 *   import { api } from "@/lib/api";
 *   api.prompts.list();
 *
 *   // or, the focused module path:
 *   import { promptsClient, type PromptTemplateSummary } from "@/lib/api/prompts";
 *   promptsClient.list();
 *
 * The implementation still lives in `lib/api.ts` -- this module is a
 * curated re-export plus typed helpers. When we eventually fully
 * extract the implementation, the inner imports here will change,
 * but the public surface won't.
 *
 * See `lib/api/README.md` for the migration model.
 */

export type {
  PromptActiveOverridesMap,
  PromptAuditRow,
  PromptCategory,
  PromptCreateRequest,
  PromptDryRunResponse,
  PromptOutputFormat,
  PromptPreviewResponse,
  PromptScope,
  PromptTemplateDetail,
  PromptTemplateSummary,
  PromptVersionBody,
  PromptVersionSummary,
  PromptActiveOverride,
} from "@/lib/api";

// The fetcher itself is just a re-export of api.prompts. We don't
// re-construct it here because the underlying apiFetch / auth-cache
// state is module-private to lib/api.ts; reaching past it would
// double-wrap auth headers + token refresh.
import { api } from "@/lib/api";
export const promptsClient = api.prompts;
