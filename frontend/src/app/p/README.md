# `/p/[project]/*` -- deprecation policy (Phase 3 IA audit)

This route tree exists as **redirect shims** -- every file in here
is ~10 lines that `redirect()` to the canonical flat URL. The
canonical pages live at:

| `/p/*` (deprecated alias) | Canonical (flat) |
|---------------------------|------------------|
| `/p/[project]`            | `/projects/[name]` |
| `/p/[project]/sprints`    | `/sprints?project=[name]` |
| `/p/[project]/sprints/[id]` | `/sprints/[id]?project=[name]` |
| `/p/[project]/stories`    | `/user-stories?project=[name]` |
| `/p/[project]/stories/[id]` | `/user-stories/[id]?project=[name]` |
| `/p/[project]/generate`   | `/generate?project=[name]` |
| `/p/[project]/generate/record` | `/generate/record?project=[name]` |
| `/p/[project]/runs`       | `/runs?project=[name]` |
| `/p/[project]/runs/[run]` | `/runs/[run]` |
| `/p/[project]/analytics`  | `/dashboard?project=[name]` |
| `/p/[project]/members`    | `/projects/[name]/members` |
| `/p/[project]/integrations` | `/projects/[name]/integrations` |

## The decision: FLAT is canonical

Per the IA audit's dual-URL-tree finding, we picked the **flat**
family as canonical because:

1. Every real page (projects/[name], sprints/[id], user-stories/[id],
   etc.) already lives on the flat tree -- the `/p/*` tree is
   redirect shims, not real implementations.
2. The command palette already deep-links to the flat tree.
3. Direct bookmarks + Cmd-K + integration deep-links + email links
   all point at the flat tree today.

## Migration timeline

  * **NOW** (this PR): both URL families work. The flat tree is
    canonical; the `/p/*` tree continues to redirect transparently.
  * **Next release**: the left sidebar's project module links
    migrate from `/p/[project]/...` to the flat URLs. Breadcrumbs
    + WorkspaceSubHeader already work on flat routes
    (Phase 2 IA audit).
  * **Release after that**: the `/p/*` redirect shims emit a
    Next.js redirect with `permanent: true` so search engines +
    third-party deep-links update.
  * **Final release**: delete the `/p/*` tree. Any stale bookmarks
    return a 404 (acceptable after two full release cycles of
    documented redirects).

## Why we don't do it all in one PR

The left sidebar (`LeftHierarchySidebar.tsx`) builds every link
template against `/p/[project]/<module>`. Migrating to flat URLs
means updating the link-builder PLUS verifying that breadcrumbs,
test-id selectors, and any third-party integration that hardcoded
`/p/...` still resolve. The audit classified this as HIGH risk and
the redirect shims are doing zero harm in the meantime -- splitting
the migration into clear stages is the safer move.

## When adding a new page

NEW pages live on the flat tree. Do not add new shims to `/p/*`.
If you need a sidebar entry for a new module, add it to
`navConfig.ts` with the flat URL; the sidebar already special-cases
the project-context query param.
