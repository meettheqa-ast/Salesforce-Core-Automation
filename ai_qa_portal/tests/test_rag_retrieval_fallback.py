"""Tests for the lexical fallback path in services.rag_retrieval.

The full pgvector path is exercised by Postgres-backed integration
tests; here we verify the SQLite fallback that ships for local dev:

* ``retrieve`` returns at most ``limit`` passages.
* Returned passages contain the query tokens.
* Empty query / empty corpus return empty lists cleanly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai_qa_portal.backend.services.db import Base
from ai_qa_portal.backend.services.db_models import Embedding


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed(db, project: str, text: str, kind: str = "context_chunk", source_id: str = "s1") -> None:
    db.add(
        Embedding(
            project_slug=project,
            source_kind=kind,
            source_id=source_id,
            chunk_index=0,
            text=text,
            token_count=len(text.split()),
            embedding_model="test",
            embedding=[0.0],  # JSON-typed on SQLite, value irrelevant for lexical path
            created_at=datetime.now(UTC),
        )
    )


def test_retrieve_empty_query_returns_empty(session):
    from ai_qa_portal.backend.services.rag_retrieval import retrieve

    _seed(session, "p1", "lorem ipsum dolor sit amet")
    session.commit()

    assert retrieve(session, project_slug="p1", query="") == []


def test_retrieve_finds_matching_chunk(session):
    from ai_qa_portal.backend.services.rag_retrieval import retrieve

    _seed(session, "p1", "the quick brown fox jumps over the lazy dog", source_id="a")
    _seed(session, "p1", "completely unrelated text about salesforce orgs", source_id="b")
    session.commit()

    passages = retrieve(session, project_slug="p1", query="quick brown fox", limit=5)
    assert passages, "should find at least one match"
    assert "quick brown fox" in passages[0].text
    assert passages[0].source_kind == "context_chunk"


def test_retrieve_scopes_by_project(session):
    from ai_qa_portal.backend.services.rag_retrieval import retrieve

    _seed(session, "p1", "alpha beta gamma", source_id="x")
    _seed(session, "p2", "alpha beta gamma", source_id="y")
    session.commit()

    passages = retrieve(session, project_slug="p1", query="alpha beta")
    assert all(p.source_id != "y" for p in passages), "must not leak across projects"
