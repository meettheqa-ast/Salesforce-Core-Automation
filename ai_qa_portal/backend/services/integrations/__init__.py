"""External-issue-tracker integration scaffolding.

This package exists to hold the contract every external system must
implement (`base.ExternalIssueProvider`) plus a registry that callers
ask "is there a provider registered for source X?".

Today no providers are registered. The Jira stub at `jira.py` is a
placeholder so a future PR knows where to land. See
`docs/storage_layout.md` and the `sprint-hierarchy-jira-ready` plan for
the migration path."""
