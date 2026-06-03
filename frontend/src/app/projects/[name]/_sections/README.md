# `_sections/` -- god-page decomposition pattern

The project home page (`page.tsx`) grew to ~1500 lines combining at
least seven distinct concerns (dashboard strip, environments, creds,
sprints, test cases, tags, saved tests). The Platform IA audit
flagged this as the #1 maintainability risk -- every change touches
the same 1500-line file and risks regressing unrelated panels.

The migration model:

1. **Each panel becomes a co-located file under `_sections/`**. The
   underscore prefix tells Next.js this is not a route -- the files
   here are page-private, not URL-addressable.
2. **Props go in; refs / state lifts where needed.** Keep state at
   the page level when multiple sections share it (open modals,
   selected rows). Each section receives only what it needs.
3. **The top-level `page.tsx` becomes a composition layer** under
   ~300 lines that imports each `_sections/<Name>.tsx`.

## Why not do it all at once?

Because the god page has dozens of intertwined state hooks (filters,
modals, busy flags, refetch triggers) and the same logic lives across
multiple panels. A wholesale extraction is multi-day mechanical work
with non-trivial regression risk. The audit classified it as MEDIUM
risk + 1-2 weeks of effort; the migration model lets us amortize it
over many follow-up changes.

## When to extract a section

Extract a section when ANY of:

- You're about to touch >100 lines of `page.tsx` for a feature.
- A panel has at least 2 useState hooks of its own.
- The panel has its own load / refresh side-effect.
- You're tempted to add a 4th tab / mode toggle to a panel.

## How

1. Create `_sections/<PanelName>.tsx` next to this README.
2. Move the JSX block + the state hooks that ONLY affect it.
3. Lift shared state (e.g. `portalProjectId`, refetch functions) as
   props from `page.tsx`.
4. Replace the original block in `page.tsx` with
   `<PanelName ...sharedProps />`.
5. Run `npm run lint` and the page in dev to confirm parity.

## Established sections (Phase 2)

- `ProjectActivitySection.tsx` -- inline ActivityFeed wrapper. Tiny
  example to validate the pattern; future extractions follow the
  same shape (props in, JSX out, no behaviour change).

## Pending decompositions (tracked in the architecture audit)

- `_sections/EnvironmentsSection.tsx` -- env list + add/select/delete
- `_sections/CredentialsSection.tsx` -- persona picker + cred fields
- `_sections/SprintsSection.tsx` -- sprint summary grid + create
- `_sections/TestCasesSection.tsx` -- filtered story-grouped TC panel
- `_sections/TagsSection.tsx` -- tag CRUD
- `_sections/SavedTestsSection.tsx` -- Robot files on disk

The same pattern applies to:

- `frontend/src/app/user-stories/[id]/_sections/`
- `frontend/src/app/generate/_sections/`
