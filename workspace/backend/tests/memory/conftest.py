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
from app.models import Workspace


class _Ref:
    """Minimal stand-in exposing `.id`, so tests read naturally."""

    def __init__(self, id: str):
        self.id = id


def _make_workspace(session, name: str) -> _Ref:
    workspace = Workspace(
        id=str(uuid.uuid4()),
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        password_hash=uuid.uuid4().hex,
    )
    session.add(workspace)
    session.flush()
    return _Ref(workspace.id)


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
