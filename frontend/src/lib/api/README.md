# `lib/api/` -- per-domain API client modules

This folder hosts the per-domain API client modules carved out of the
monolithic [`lib/api.ts`](../api.ts). The goal (per the Platform IA
audit, Phase 2) is to make each domain's client surface independently
findable, reviewable, and testable.

## Migration model

The legacy [`lib/api.ts`](../api.ts) stays in place and continues to
export the entire `api` namespace + every type. Per-domain modules in
this folder are **additive**:

- Existing imports (`import { api, type ProjectRow } from "@/lib/api";`)
  keep working unchanged.
- New code can import the focused module directly
  (`import { promptsClient } from "@/lib/api/prompts";`) or stick with
  the umbrella -- both end up at the same implementation.

Each module is self-contained:

- All types it owns
- The fetcher object (a slice of the `api.*` namespace)
- Module-level JSDoc explaining the domain

Domains land here when they're next touched by an unrelated change,
so the migration cost is amortised. A wholesale move-everything pass
is HIGH risk + zero feature value.

## Current modules

| Module | Domain | Source extracted from |
|--------|--------|-----------------------|
| `prompts.ts` | AI prompt registry CRUD + dry-run + audit | `api.prompts.*` |
| `imports.ts` | CSV/Excel test-case import wizard | `api.imports.*` |

## When to extract a new module

Extract a domain when ANY of:

- The domain has > ~200 lines of client code in `lib/api.ts`.
- You're about to add 3+ new endpoints to it.
- The domain has its own page tree (e.g. `/settings/prompts`) and the
  page tests import a lot of the same types together.

Keep types co-located with the fetcher in the same `lib/api/{domain}.ts`
file so a single import gives you the full surface for that domain.
