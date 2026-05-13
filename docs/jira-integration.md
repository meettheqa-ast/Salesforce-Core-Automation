# Jira integration

The portal can pull every sprint, issue, and comment from a Jira Cloud
project into Postgres, then feed it back to the AI as RAG context when
generating test cases.

## Auth & scope

Two scopes are supported:

* **Org-wide**: one connection configured by an admin at `Settings →
  Integrations`. Used as the default for any project that doesn't
  configure its own.
* **Per-project**: override the org-wide connection from
  `Projects → {name} → Integrations → Jira`. Useful when one Salesforce
  project maps to a customer-specific Jira tenant.

Credentials are HTTP Basic (`email:api_token`). Generate the token at
[id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
Tokens are encrypted with Fernet (`FERNET_KEY`) at rest and never echoed
back by any endpoint after creation.

## What "Sync now" pulls

1. The Jira project metadata (`/rest/api/3/project/{key}`).
2. Every board for the project, then every sprint
   (`/rest/agile/1.0/board/{id}/sprint`).
3. Every issue (`/rest/api/3/search` with `jql=project="KEY"`),
   `expand=renderedFields,names` so the raw ADF body is preserved.
4. Every comment on each issue (`/rest/api/3/issue/{key}/comment`).

Everything is upserted by Jira id, so re-syncing the same project is
idempotent. After the SQL transaction commits, the same content is
indexed into the pgvector `embeddings` table (see
[rag-architecture.md](rag-architecture.md)).

## Import into portal entities

The raw Jira tables don't yet live in the portal hierarchy. After a sync
the UI shows imported sprints / issues and lets you promote them:

* Jira sprint → portal `Sprint` (linked via `external_id` = Jira id).
* Jira Story/Task/Bug → portal `UserStory`.
* Jira Sub-task → kept inside the parent issue's `payload`; not
  promoted to portal entities for v1.

The promotion endpoint is `POST /projects/{slug}/integrations/jira/import`.

## Rate limits and large projects

Atlassian's documented limit is 500 requests / 5 minutes / IP. We
serialise calls and respect `Retry-After` on 429. A full sync of a
1000-issue project takes roughly 3-5 minutes; expect the request thread
to be blocked for that duration. Long syncs should be triggered from
the UI's "Sync now" button rather than from a hot path.
