# -*- coding: utf-8 -*-
"""Shared fixtures for the PAI memory tests.

Imported by `tests/conftest.py` so these are available to every test module
without each one re-declaring them.

Deliberately does not reuse the existing `workspace` fixture: that one goes
through the HTTP API and returns a dict, while these tests work directly
against services and need an object with `.id`. Creating rows straight through
the ORM also keeps them fast and independent of the routers.
"""

import uuid

import pytest

from app.memory.field_definitions import SEED_FIELD_DEFINITIONS, VaultFieldDefinitionService
from app.models import User, Workspace


class _Ref:
    """Minimal stand-in exposing `.id`, so tests read naturally.

    `owner_source` is the event source the workspace's own student posts
    under. Extraction only ingests that one (see
    app/memory/extraction_context), because `human:` is a namespace rather
    than a person — an external Slack sender also lands in it.
    """

    def __init__(self, id: str, owner_source: str = ""):
        self.id = id
        self.owner_source = owner_source


def _make_workspace(session, name: str) -> _Ref:
    owner = User(email=f"{uuid.uuid4().hex[:10]}@example.com")
    session.add(owner)
    session.flush()
    workspace = Workspace(
        id=str(uuid.uuid4()),
        owner_user_id=owner.id,
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        password_hash=uuid.uuid4().hex,
    )
    session.add(workspace)
    session.flush()
    return _Ref(workspace.id, owner_source=f"human:{owner.id}")


@pytest.fixture
def db_session(db):
    """Alias for the shared session fixture, named for readability here."""
    return db


@pytest.fixture
def workspace(db):
    """A workspace created directly through the ORM."""
    return _make_workspace(db, "Memory Test")


@pytest.fixture
def other_workspace(db):
    """A second workspace — every isolation test needs somewhere to leak to."""
    return _make_workspace(db, "Other Student")


@pytest.fixture(autouse=True)
def _bind_session_factory():
    """Point the background/tool session factory at the test database.

    Out-of-request code (worker, Operator's background task, memory tools)
    calls `app.database.new_session()` rather than the injected request
    session. Without this it would open sessions against the *production*
    engine — a different database — and every assertion about what it wrote
    would fail for the wrong reason.

    One override, applied automatically, because `new_session()` reads the
    factory at call time. Nothing imports the factory at module scope, so
    there is nothing else to patch.
    """
    from tests.conftest import TestingSessionLocal
    from app.database import set_session_factory

    previous = set_session_factory(TestingSessionLocal)
    yield TestingSessionLocal
    set_session_factory(previous)


@pytest.fixture
def seed_fields(db):
    """Install the standard field definitions.

    Migration 055 seeds these in production; tests build their schema from
    `Base.metadata.create_all`, which does not run migrations, so the same
    definitions are inserted here from the one shared source.
    """
    service = VaultFieldDefinitionService(db)
    for spec in SEED_FIELD_DEFINITIONS:
        service.upsert_definition(spec)
    db.flush()
    return service
