# Cursor AI Workflow Modes

This folder defines five specialized AI operating profiles for working on the Salesforce QA Automation platform. Each mode is a focused `.mdc` rule with `alwaysApply: false`, so none of them pollute the context until you explicitly invoke them.

Invoke a mode by `@`-mentioning the rule filename in chat:

```
@caveman ...
@architect ...
@auditor ...
@ux ...
@debug ...
```

Modes are composable: ask `@architect` to plan, then `@caveman` to implement, then `@debug` if something breaks.

---

## Mode summary

| Mode | Purpose | Best Cursor mode | Best model | Output type |
|---|---|---|---|---|
| `@caveman` | Compressed implementation, repo-wide edits, batch fixes | **Agent** | **GPT 5.3 Codex** (fallback: Claude Opus 4.7) | Diffs |
| `@architect` | System design, scaling, refactor strategy, deployment | **Ask** (or Agent if writing a doc) | **Claude Opus 4.7** | Written plan |
| `@auditor` | Gap detection: dead buttons, orphan endpoints, contract drift | **Agent** | **GPT 5.3 Codex** (fallback: Claude Opus 4.7 for complex contract drift) | Audit report |
| `@ux` | Navigation, hierarchy, flow simplification, dashboard coherence | **Ask** or **Agent** | **Claude Opus 4.7** | UX review / storyboard |
| `@debug` | Root-cause debugging, async / MCP / browser / pipeline failures | **Agent** | **GPT 5.3 Codex** (fallback: Claude Opus 4.7) | Diagnosis + minimal fix |

---

## When to use each mode

### `@caveman`
- **Use when:** the design is decided and the work is mechanical — renames, contract wiring, repetitive fixes, dead-code removal, batch refactors across many files.
- **Do not use when:** the task hides a real design choice, or when you need to discuss tradeoffs. It will ship the first reasonable answer, which is not always the right answer.

### `@architect`
- **Use when:** you are about to commit to a structural change — new subsystem, new tenancy model, new transport, deprecation plan, integration topology.
- **Do not use when:** you already know the shape and just need code. The planning overhead will slow you down.

### `@auditor`
- **Use when:** you suspect the product is more half-built than the file count suggests — orphan endpoints, dead buttons, contract drift between frontend and backend, partial features.
- **Do not use when:** you want a style / lint pass. This mode reports user-visible breakage, not nits.

### `@ux`
- **Use when:** the portal is growing faster than its information architecture, or a flow has accumulated friction over many small additions.
- **Do not use when:** the problem is "this button is broken" — that is `@auditor` or `@debug`, not UX.

### `@debug`
- **Use when:** a failure resists a one-shot fix, especially around async, MCP, browser automation, or the generation pipeline.
- **Do not use when:** the fix is obvious from the stack trace. `@caveman` will ship it faster.

---

## Recommended workflows

### New feature, end-to-end
1. `@architect` — write the plan, get sign-off
2. `@caveman` — implement against the plan
3. `@debug` — only if a failure surfaces during integration
4. `@auditor` — final sweep to catch any wire-up gaps

### Cleaning up an existing surface
1. `@auditor` — produce the gap list
2. `@ux` — decide which gaps are flow problems vs. plumbing problems
3. `@caveman` — fix the plumbing
4. `@architect` — only if the gaps reveal a structural issue

### Production incident
1. `@debug` — reproduce, localize, fix, prove
2. `@caveman` — apply the same fix anywhere else it is latent
3. `@architect` — only if the incident exposes a design flaw worth a follow-up plan

---

## Composition rules

- Modes never conflict with each other — they are not loaded simultaneously, you invoke one per turn.
- Modes are silent until invoked — none use `alwaysApply: true`.
- A mode can hand off to another mode mid-session by saying so explicitly. The handoff is a message to the user, not an automatic switch.
- Project-wide standards that should *always* apply (e.g. "use snake_case in Python files") belong in their own `alwaysApply: true` rule, not inside a mode.

---

## Cursor-specific notes for large multi-folder repos

These apply regardless of which mode is active:

- **Prefer `Glob` and `Grep` (ripgrep) over `find`/`grep` shell calls.** They respect `.gitignore` and are an order of magnitude faster on this tree.
- **Never read `frontend/.next/`, `Results/`, `_local_data/`, `__pycache__/`, `venv/`, or `node_modules/`.** They are generated, large, and waste context.
- **Pin the agent's attention with file paths, not file names.** `ai_qa_portal/backend/routers/generate.py` is unambiguous; `generate.py` is not.
- **For repo-wide renames, list all hits first, then apply.** This repo has parallel paths under `ai_qa_portal/backend/` and a stale duplicate tree at the root — easy to miss half.
- **Keep API-contract changes atomic.** A FastAPI route, its Pydantic model, the Alembic migration if any, and the typed client in `frontend/src/lib/api.ts` should change in the same turn.
- **Background long-running shells** (dev servers, watchers) instead of blocking on them.
- **One mode per turn.** Switching modes mid-turn fragments reasoning. Finish the turn, then switch.

---

## Adding a new mode

1. Create `.cursor/rules/<name>.mdc` with frontmatter:
   ```yaml
   ---
   description: One sentence on when to invoke. Be specific — this is what the agent reads to decide.
   globs:
   alwaysApply: false
   ---
   ```
2. Keep it focused on one job. If the description needs "and", split it.
3. Include: operating principles, output shape, anti-patterns, when-to-hand-off, model pairing, example invocations.
4. Update the table at the top of this README.
