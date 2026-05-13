"""ORM models for Jira/GitHub integration, scheduling, and the RAG context store.

Splitting these out of ``services/db.py`` keeps that file focused on the
original auth + RBAC + run-index tables; every new feature surface adds a
module here. Each module attaches its tables to the shared ``Base.metadata``
in ``services/db.py``, so a single ``Base.metadata.create_all`` or Alembic
``--autogenerate`` sweep sees all of them.

Imported for side effect of registering models on ``Base.metadata``.
"""

from __future__ import annotations

from .context import ContextFile, ContextFileRow
from .embeddings import Embedding, EmbeddingSourceKind
from .github import GitHubAuthKind, GitHubConnection, GitHubRepo, GitHubScope
from .jira import (
    JiraComment,
    JiraConnection,
    JiraIssue,
    JiraProject,
    JiraScope,
    JiraSprint,
)
from .schedules import Schedule, ScheduleRun, ScheduleRunner, ScheduleStatus, ScheduleTargetKind
from .test_data import TestDataRow, TestDataTable
from .types import JSONColumn, VectorColumn

__all__ = [
    "ContextFile",
    "ContextFileRow",
    "Embedding",
    "EmbeddingSourceKind",
    "GitHubAuthKind",
    "GitHubConnection",
    "GitHubRepo",
    "GitHubScope",
    "JSONColumn",
    "JiraComment",
    "JiraConnection",
    "JiraIssue",
    "JiraProject",
    "JiraScope",
    "JiraSprint",
    "Schedule",
    "ScheduleRun",
    "ScheduleRunner",
    "ScheduleStatus",
    "ScheduleTargetKind",
    "TestDataRow",
    "TestDataTable",
    "VectorColumn",
]
