# -*- coding: utf-8 -*-
"""
Test fixtures for the workspace backend.

Uses SQLite in-memory database for fast isolated tests.
Registers custom compilers so PostgreSQL-specific types work with SQLite.
Uses StaticPool to share a single in-memory database across all connections.
"""

from datetime import datetime, timezone

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
    """Create all tables before each test, drop after.

    Also points `app.database.new_session()` at the test engine. Overriding the
    `get_db` dependency only redirects request-scoped sessions; background and
    tool code calls `new_session()` directly and would otherwise talk to the
    production engine from inside a test — which is the exact failure
    `set_session_factory` exists to prevent (see app/database.py).
    """
    from app.database import set_session_factory

    Base.metadata.create_all(bind=engine)
    previous = set_session_factory(TestingSessionLocal)
    try:
        yield
    finally:
        set_session_factory(previous)
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



def make_owned_workspace(name="Test Workspace", agent_name="agent-alpha",
                         email="test@example.com"):
    """Build an owned workspace directly, in the shape POST /v1/workspaces
    used to return.

    That endpoint now only provisions the *calling* student's own workspace and
    refuses anonymous callers, so tests that just need "a workspace with this
    agent in it" can no longer go through it. Building the row here keeps that
    setup possible without reopening an anonymous creation path — and keeps
    tests/test_workspace_creation_invariants.py meaningful, since it asserts
    what the *API* can produce.

    Always sets `owner_user_id`: an ownerless active workspace is exactly the
    state the product no longer has.
    """
    import secrets as _secrets
    from app.models import Channel, ChannelMember, User, Workspace, WorkspaceMember

    agent_session_id = _secrets.token_hex(16)
    session = TestingSessionLocal()
    try:
        owner = session.query(User).filter(User.email == email).one_or_none()
        if owner is None:
            owner = User(email=email, username=email.split("@")[0].replace(".", "")[:32])
            session.add(owner)
            session.flush()

        ws = Workspace(
            slug=_secrets.token_hex(4),
            name=name,
            owner_user_id=owner.id,
            password_hash=_secrets.token_urlsafe(32),
            require_login=True,
            settings={},
            status="active",
        )
        session.add(ws)
        session.flush()

        channel_payload = None
        if agent_name:
            session.add(WorkspaceMember(
                workspace_id=ws.id, agent_name=agent_name,
                role="master", status="online",
                # A live join session. Public event ingress identifies an agent
                # by this, never by a name in the request body — see
                # app/event_identity.py — so a fixture agent needs one to speak.
                session_id=agent_session_id,
                session_started_at=datetime.now(timezone.utc),
                # `status` alone does not mean live: _member_is_online also
                # wants a fresh heartbeat, and without one the workspace looks
                # agent-less and posts "no agent online" system notices.
                last_heartbeat=datetime.now(timezone.utc),
            ))
            channel = Channel(
                workspace_id=ws.id,
                name=f"session-{_secrets.token_hex(4)}",
                title="Session 1",
                created_by=agent_name,
                master_agent=agent_name,
                status="active",
            )
            session.add(channel)
            session.flush()
            session.add(ChannelMember(channel_id=channel.id, agent_name=agent_name))
            session.flush()
            channel_payload = {
                "channelId": str(channel.id),
                "workspaceId": str(ws.id),
                "name": channel.name,
                "title": channel.title,
                "masterAgent": channel.master_agent,
                "createdBy": channel.created_by,
                "status": channel.status,
                "orchestrationMode": channel.orchestration_mode or "dynamic",
                "participants": [agent_name],
            }
        # Mirror provision_workspace: a real workspace comes with PAI Counselor
        # (a no-op when PAI is disabled or unkeyed, which is the default).
        try:
            from app.services.pai import provision_pai, seed_welcome_thread
            if provision_pai(session, ws):
                seed_welcome_thread(session, ws)
        except Exception:
            pass
        session.commit()

        return {
            "workspaceId": str(ws.id),
            "slug": ws.slug,
            "name": ws.name,
            "token": ws.password_hash,
            "sessionId": agent_session_id if agent_name else None,
            # Same value under the snake_case key too: some suites keep the API
            # shape this helper returns, others re-key it, and the agent session
            # is now needed by both.
            "session_id": agent_session_id if agent_name else None,
            "agentName": agent_name,
            "channel": channel_payload,
        }
    finally:
        session.close()


@pytest.fixture
def workspace(client):
    """An owned workspace with one agent and a starter channel."""
    data = make_owned_workspace()
    return {
        "id": data["workspaceId"],
        "slug": data["slug"],
        "name": data["name"],
        "token": data["token"],
        # The fixture agent's live session. Event ingress needs it to know
        # WHICH agent is posting; the workspace token alone is shared and
        # identifies nobody.
        "session_id": data["sessionId"],
        "agent_name": data["agentName"],
        "channel": data["channel"],
    }
