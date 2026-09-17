# -*- coding: utf-8 -*-
"""
Test fixtures for the workspace backend.

Uses SQLite in-memory database for fast isolated tests.
Registers custom compilers so PostgreSQL-specific types work with SQLite.
Uses StaticPool to share a single in-memory database across all connections.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Register PostgreSQL types for SQLite compilation
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

# Now import the app (which loads models)
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Test database setup — StaticPool ensures all connections share one DB
# ---------------------------------------------------------------------------

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Translate Postgres-specific server defaults (NOW(), gen_random_uuid()) to
# SQLite-friendly equivalents at SQL-emit time. Production uses Postgres and
# is unaffected — this only fires for the test engine. Without this, CREATE
# TABLE fails because SQLite doesn't recognize NOW().
@event.listens_for(engine, "before_cursor_execute", retval=True)
def _rewrite_pg_to_sqlite(conn, cursor, statement, parameters, context, executemany):
    if "DEFAULT NOW()" in statement:
        statement = statement.replace("DEFAULT NOW()", "DEFAULT CURRENT_TIMESTAMP")
    if "DEFAULT gen_random_uuid()" in statement:
        # SQLite has no UUID generator; Python-side default=_uuid handles INSERTs.
        statement = statement.replace("DEFAULT gen_random_uuid()", "")
    return statement, parameters


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def db():
    """Direct database session for test setup/assertions."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def workspace(client):
    """Create a workspace and return its details (id, slug, token).

    Owned by test@example.com. Ownership is what grants a human access now
    (`workspaces.owner_user_id` — see app/access.py); `creator_email` is
    display only. The bearer-auth tests mock identity as that same address, so
    the fixture has to model a real, owned workspace rather than the orphaned
    one anonymous creation produces.
    """
    resp = client.post("/v1/workspaces", json={
        "name": "Test Workspace",
        "agent_name": "agent-alpha",
        "creator_email": "test@example.com",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]

    from app.models import User, Workspace as WorkspaceModel
    session = TestingSessionLocal()
    try:
        owner = session.query(User).filter(User.email == "test@example.com").one_or_none()
        if owner is None:
            owner = User(email="test@example.com", username="testuser")
            session.add(owner)
            session.flush()
        ws = session.get(WorkspaceModel, data["workspaceId"])
        ws.owner_user_id = owner.id
        session.commit()
    finally:
        session.close()

    return {
        "id": data["workspaceId"],
        "slug": data["slug"],
        "name": "Test Workspace",
        "token": data["token"],
        "channel": data["channel"],
    }
