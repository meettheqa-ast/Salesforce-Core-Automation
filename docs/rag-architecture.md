# RAG architecture

The portal grounds every generation in the project's real context:
Jira issues + comments, uploaded docs, and "test data" tables. This
happens through a single pgvector-backed embedding store.

## Stack

* **Postgres + pgvector**: the `embeddings` table holds one row per
  text chunk with a `VECTOR(1536)` column and an IVFFlat cosine index.
* **Embedding providers**: pluggable via `EMBEDDING_PROVIDER`.
  Defaults to `openai` (`text-embedding-3-small`, 1536-dim). Also
  supported: `gemini` (768-dim) and `ollama` (768-dim local). When you
  switch dimension you must drop and re-create the table — the Alembic
  migration reads `EMBEDDING_DIM` to size the column.
* **Chunker**: `services/context_parser.chunk_text` does token-sized
  windows (default 800 tokens, 100-token overlap) via `tiktoken`. On
  hosts without `tiktoken` we fall back to character chunking.

## What gets indexed

| Source                  | Index trigger                                                 |
|-------------------------|---------------------------------------------------------------|
| Jira issues + comments  | After each `Sync now` call (`services/jira_sync`).            |
| Context-file chunks     | At upload time (`routers/context_files`).                     |
| Test-data table rows    | At upload time (`routers/test_data`).                         |
| Portal user stories     | Manually via `services/rag_index.index_user_story` (extension point).|
| Portal test cases       | Manually via `services/rag_index.index_test_case`.            |

Each `(project_slug, source_kind, source_id)` triple is upserted
idempotently: re-running the indexer deletes prior rows for that
source before inserting fresh chunks.

## Retrieval

`services/rag_retrieval.retrieve(db, project_slug=..., query=...,
sprint_id=?, story_id=?, limit=8)` returns ranked `Passage` objects:

1. Embed the query.
2. KNN over rows matching `project_slug` and the requested source
   kinds, using `embedding.cosine_distance`.
3. If `sprint_id` or `story_id` is given, run a second pre-filtered
   query and merge results so active-scope chunks bubble to the top.

The prompt assembler (`prompts/assembler.build_user_prompt_with_catalog`)
accepts a `rag_context` string and renders it as `## Project context`
above the keyword catalog. Callers that want RAG simply pass
`format_passages_block(retrieve(...))` through.

## Wired-in callers

* `services/test_case_generator.TestCaseGenerator.generate(story, db=..., project_slug=...)`
  — drafter pulls RAG context using the story's title + description.
* `services/test_case_script_builder.TestCaseScriptBuilder.build_robot_script(tc, ..., db=..., project_slug=...)`
  — builder pulls RAG context using the test case's title + expected
  result.

Both arguments are optional; callers that don't pass them get the
legacy zero-context behaviour.

## SQLite fallback

For local dev without Postgres, the schema still creates (the vector
column degrades to JSON) but pgvector cosine queries don't work. The
retrieval module falls back to a token-overlap substring scan over
`embeddings.text` so unit tests can run without standing up Postgres.

## Re-indexing

A future admin endpoint will re-embed everything for a project
(useful after switching providers / models). For now, `services/rag_index`
exposes per-source helpers that can be called from a script:

```python
from ai_qa_portal.backend.services import rag_index
from ai_qa_portal.backend.services.db import SessionLocal

with SessionLocal() as db:
    for issue in db.query(JiraIssue).filter_by(connection_id=...).all():
        rag_index.index_jira_issue(
            db,
            project_slug="my-project",
            issue_id=issue.jira_id,
            summary=issue.summary,
            description=issue.description,
            sprint_jira_id=issue.sprint_jira_id,
        )
```
