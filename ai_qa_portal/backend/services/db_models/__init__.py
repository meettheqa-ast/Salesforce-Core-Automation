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
from .generation import GenerationJob, GenerationMetric, GenerationStatus
from .heal import HealEvent, OrgFieldLearning
from .imports import ImportBatch
from .prompts import (
    PromptMeta,
    PromptOverride,
    PromptTemplate,
    PromptUsageAudit,
    PromptVersion,
)
from .github import GitHubAuthKind, GitHubConnection, GitHubRepo, GitHubScope
from .planner import KeywordOutcome, VerifiedRecipe
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
    "GenerationJob",
    "GenerationMetric",
    "GenerationStatus",
    "HealEvent",
    "ImportBatch",
    "PromptMeta",
    "PromptOverride",
    "PromptTemplate",
    "PromptUsageAudit",
    "PromptVersion",
    "GitHubAuthKind",
    "KeywordOutcome",
    "VerifiedRecipe",
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
    "OrgFieldLearning",
    "VectorColumn",
]
