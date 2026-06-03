/**
 * Focused client module for the CSV / Excel test-case import wizard.
 *
 * See `lib/api/README.md` for the migration model. The implementation
 * still lives in `lib/api.ts`; this module is a curated re-export
 * that lets the wizard import everything it needs from one place.
 */

export type {
  ImportBatchDetail,
  ImportBatchSummary,
  ImportCommitResponse,
  ImportFailedRow,
  ImportParseResponse,
} from "@/lib/api";

import { api } from "@/lib/api";
export const importsClient = api.imports;
