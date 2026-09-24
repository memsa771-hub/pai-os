# -*- coding: utf-8 -*-
"""Shared SQLite-backed session for document tests.

Mirrors `tests/test_student_records.py`: real SQL persistence, fast, and with
the same tables the document pipeline actually touches.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    BackgroundJob, DocumentArtifact, EventRecord, FileRecord, MemoryCandidate,
    PaiEpisode, PaiMemory, ProfileIssue, StudentRecordRevision, User, VaultFact,
    VaultFieldDefinition, Workspace,
)
from app.memory.field_definitions import SEED_FIELD_DEFINITIONS, VaultFieldDefinitionService
from app.memory.student_records import ENTITY_MODELS


@pytest.fixture
def db():
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.dialects.postgresql import JSONB

    @compiles(JSONB, "sqlite")
    def compile_jsonb(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def setup(connection, record):  # noqa: ARG001
        connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())
        connection.execute("PRAGMA foreign_keys=ON")

    tables = [
        User, Workspace, EventRecord, FileRecord, VaultFact, VaultFieldDefinition,
        MemoryCandidate, PaiMemory, PaiEpisode, ProfileIssue, StudentRecordRevision,
        BackgroundJob, DocumentArtifact, *ENTITY_MODELS.values(),
    ]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in tables])

    with Session(engine, autoflush=False) as session:
        user = User(email="student@example.test")
        session.add(user)
        session.flush()
        workspace = Workspace(name="Student", owner_user_id=user.id)
        other = Workspace(name="Other student")
        session.add_all([workspace, other])
        session.flush()
        session.info.update(workspace=workspace.id, other=other.id, user=user.id)
        fields = VaultFieldDefinitionService(session)
        for spec in SEED_FIELD_DEFINITIONS:
            fields.upsert_definition({**spec, "context_tags": [], "profile_priority": 50})
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def workspace_id(db):
    return db.info["workspace"]


def make_file(db, workspace_id: str, filename: str, data: bytes,
              content_type: str = "application/pdf") -> FileRecord:
    """Persist a FileRecord the way an upload route would."""
    import uuid

    record = FileRecord(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        filename=filename,
        content_type=content_type,
        size=len(data),
        storage_key=f"test/{filename}",
        uploaded_by="human:student",
    )
    db.add(record)
    db.flush()
    return record
