# -*- coding: utf-8 -*-
"""The client says what happened. The server decides who said it.

`POST /v1/events` used to read `source` from the request body and persist and
route it as the event's identity, so anyone holding the workspace token could
speak as PAI Counselor, as the system, or as another agent. Identity is now
derived from credentials alone (app/event_identity.py) and the body's value is
discarded.

These tests are written against the HTTP surface on purpose: the claim is about
what a CALLER can produce, so exercising the resolver directly would prove
nothing about the door.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.access as access
import app.event_identity as event_identity
from app.models import EventRecord, User, Workspace, WorkspaceMember
from tests.conftest import make_owned_workspace


def _stub_identity(monkeypatch, mapping):
    """bearer -> claims, for both modules that verify identity."""
    resolve = lambda tok: mapping.get(tok)
    monkeypatch.setattr(event_identity, "verify_identity_claims", resolve)
    monkeypatch.setattr(access, "verify_identity_claims", resolve)


def _claims(email):
    return {"provider": "supabase", "email": email, "supabase_uid": "uid",
            "apple_sub": None, "display_name": "Student"}


def _owner_headers(monkeypatch, email="test@example.com", bearer="owner-tok"):
    _stub_identity(monkeypatch, {bearer: _claims(email)})
    return {"Authorization": f"Bearer {bearer}"}


def _post(client, workspace, headers, **overrides):
    body = {
        "type": "workspace.message.posted",
        "target": f"channel/{workspace['channel']['name']}",
        "payload": {"content": "hello"},
        "network": workspace["id"],
    }
    body.update(overrides)
    return client.post("/v1/events", json=body, headers=headers)


def _stored_source(db, workspace_id, content):
    row = db.execute(
        select(EventRecord).where(
            EventRecord.network_id == workspace_id,
            EventRecord.type == "workspace.message.posted",
        )
    ).scalars().all()
    for event in row:
        if (event.payload or {}).get("content") == content:
            return event.source
    return None


# ---------------------------------------------------------------------------
# A claimed identity is not an identity
# ---------------------------------------------------------------------------

class TestBodySourceIsIgnored:
    def test_owner_claiming_pai_is_stored_as_the_human(self, client, db, workspace, monkeypatch):
        """(1) body source=openagents:pai -> human:<real-user-id>."""
        headers = _owner_headers(monkeypatch)
        resp = _post(client, workspace, headers,
                     source="openagents:pai",
                     payload={"content": "claim-pai"})
        assert resp.status_code == 200

        owner = db.execute(select(User).where(User.email == "test@example.com")).scalar_one()
        assert resp.json()["data"]["source"] == f"human:{owner.id}"
        db.rollback()
        assert _stored_source(db, workspace["id"], "claim-pai") == f"human:{owner.id}"

    def test_owner_cannot_create_a_system_event(self, client, db, workspace, monkeypatch):
        """(2) body source=system:admin -> not a system event."""
        headers = _owner_headers(monkeypatch)
        resp = _post(client, workspace, headers,
                     source="system:admin",
                     payload={"content": "claim-system"})
        assert resp.status_code == 200

        stored = resp.json()["data"]["source"]
        assert not stored.startswith("system:")
        assert stored.startswith("human:")
        db.rollback()
        assert not (_stored_source(db, workspace["id"], "claim-system") or "").startswith("system:")

    def test_agent_cannot_impersonate_another_agent(self, client, db, workspace):
        """(5) agent-alpha's session + source=openagents:agent-beta -> alpha."""
        client.post("/v1/join", json={
            "agent_name": "agent-beta", "token": workspace["token"],
            "network": workspace["id"],
        })
        resp = _post(
            client, workspace,
            {"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
            source="openagents:agent-beta",
            payload={"content": "impersonate"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "openagents:agent-alpha"
        db.rollback()
        assert _stored_source(db, workspace["id"], "impersonate") == "openagents:agent-alpha"

    def test_agent_cannot_claim_a_system_source(self, client, workspace):
        resp = _post(
            client, workspace,
            {"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
            source="system:workspace",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "openagents:agent-alpha"


# ---------------------------------------------------------------------------
# Agent identity comes from the session, and only from a live one
# ---------------------------------------------------------------------------

class TestAgentSessionBinding:
    def test_valid_session_names_the_agent(self, client, workspace):
        """(4) token + current session -> openagents:<that agent>."""
        join = client.post("/v1/join", json={
            "agent_name": "agent-gamma", "token": workspace["token"],
            "network": workspace["id"],
        }).json()["data"]

        resp = _post(
            client, workspace,
            {"X-Workspace-Token": workspace["token"], "X-Session-Id": join["session_id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "openagents:agent-gamma"

    def test_stale_session_is_rejected(self, client, workspace):
        """(6) a newer join rotates the session; the old one stops working."""
        first = client.post("/v1/join", json={
            "agent_name": "agent-delta", "token": workspace["token"],
            "network": workspace["id"],
        }).json()["data"]["session_id"]
        second = client.post("/v1/join", json={
            "agent_name": "agent-delta", "token": workspace["token"],
            "network": workspace["id"],
        }).json()["data"]["session_id"]
        assert first != second

        stale = _post(client, workspace,
                      {"X-Workspace-Token": workspace["token"], "X-Session-Id": first})
        assert stale.status_code == 401

        fresh = _post(client, workspace,
                      {"X-Workspace-Token": workspace["token"], "X-Session-Id": second})
        assert fresh.status_code == 200

    def test_missing_session_is_rejected(self, client, workspace):
        """(7) token alone is shared by every agent and names nobody."""
        resp = _post(client, workspace, {"X-Workspace-Token": workspace["token"]})
        assert resp.status_code == 401

    def test_session_from_another_workspace_is_rejected(self, client, workspace):
        """(8) the session lookup is scoped to this workspace."""
        other = make_owned_workspace(name="Other", agent_name="agent-other",
                                     email="other@example.com")
        resp = _post(
            client, workspace,
            {"X-Workspace-Token": workspace["token"], "X-Session-Id": other["sessionId"]},
        )
        assert resp.status_code == 401

    def test_other_workspaces_token_is_rejected(self, client, workspace):
        other = make_owned_workspace(name="Other2", agent_name="agent-other2",
                                     email="other2@example.com")
        resp = _post(
            client, workspace,
            {"X-Workspace-Token": other["token"], "X-Session-Id": workspace["session_id"]},
        )
        assert resp.status_code == 401

    def test_connector_metadata_session_still_works(self, client, workspace):
        """The shipped agent-connector sends it in metadata, not a header."""
        resp = client.post("/v1/events", json={
            "type": "workspace.message.posted",
            "target": f"channel/{workspace['channel']['name']}",
            "payload": {"content": "via metadata"},
            "metadata": {"session_id": workspace["session_id"]},
            "network": workspace["id"],
        }, headers={"X-Workspace-Token": workspace["token"]})
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "openagents:agent-alpha"


# ---------------------------------------------------------------------------
# Humans
# ---------------------------------------------------------------------------

class TestHumanIdentity:
    def test_non_owner_cannot_post_into_another_workspace(self, client, db, workspace, monkeypatch):
        """(3) User A cannot post into User B's workspace."""
        db.add(User(email="intruder@example.com"))
        db.commit()
        headers = _owner_headers(monkeypatch, email="intruder@example.com", bearer="intruder-tok")

        resp = _post(client, workspace, headers)
        assert resp.status_code == 401

    def test_anonymous_cannot_post(self, client, workspace):
        assert _post(client, workspace, {}).status_code == 401

    def test_payload_display_name_is_not_identity(self, client, db, workspace, monkeypatch):
        """Presentation metadata may stay; it just decides nothing."""
        headers = _owner_headers(monkeypatch)
        resp = _post(client, workspace, headers, payload={
            "content": "display-name-test",
            "sender_name": "Someone Else",
            "sender_email": "victim@example.com",
        })
        assert resp.status_code == 200

        owner = db.execute(select(User).where(User.email == "test@example.com")).scalar_one()
        assert resp.json()["data"]["source"] == f"human:{owner.id}"


# ---------------------------------------------------------------------------
# Reserved identities
# ---------------------------------------------------------------------------

class TestReservedAgentNames:
    def test_cannot_join_as_pai(self, client, workspace):
        """Joining is what mints a session, so the refusal has to live there.

        Otherwise a token holder joins as `pai`, gets a genuine session, and
        posts as PAI Counselor through the front door.
        """
        resp = client.post("/v1/join", json={
            "agent_name": "pai", "token": workspace["token"], "network": workspace["id"],
        })
        assert resp.status_code == 403

    def test_cannot_join_as_pai_operator(self, client, workspace):
        resp = client.post("/v1/join", json={
            "agent_name": "pai-operator", "token": workspace["token"],
            "network": workspace["id"],
        })
        assert resp.status_code == 403

    def test_reserved_names_are_case_insensitive(self, client, workspace):
        resp = client.post("/v1/join", json={
            "agent_name": "PAI", "token": workspace["token"], "network": workspace["id"],
        })
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Trusted server code still speaks
# ---------------------------------------------------------------------------

class TestInternalEventsStillWork:
    def test_internal_emit_can_use_a_system_source(self, db, workspace):
        """(11) what public ingress refuses, trusted code may still do."""
        from app.event_identity import emit_internal_event

        ws = db.get(Workspace, workspace["id"])
        result = asyncio.run(emit_internal_event(
            db, ws,
            type="workspace.message.posted",
            source="system:workspace",
            target=f"channel/{workspace['channel']['name']}",
            payload={"content": "system notice", "message_type": "chat"},
        ))
        db.commit()
        assert result is not None
        assert result.source == "system:workspace"
        db.rollback()
        assert _stored_source(db, workspace["id"], "system notice") == "system:workspace"

    def test_internal_emit_can_speak_as_pai(self, db, workspace):
        """(9) PAI Counselor's own voice is a server-side capability."""
        from app.event_identity import emit_internal_event

        ws = db.get(Workspace, workspace["id"])
        result = asyncio.run(emit_internal_event(
            db, ws,
            type="workspace.message.posted",
            source="openagents:pai",
            target=f"channel/{workspace['channel']['name']}",
            payload={"content": "counsellor reply", "message_type": "chat"},
        ))
        db.commit()
        assert result.source == "openagents:pai"
        db.rollback()
        assert _stored_source(db, workspace["id"], "counsellor reply") == "openagents:pai"

    def test_public_ingress_cannot_reach_those_sources(self, client, workspace):
        """The same two sources, attempted from outside, resolve to the agent."""
        for claimed in ("openagents:pai", "system:workspace"):
            resp = _post(
                client, workspace,
                {"X-Workspace-Token": workspace["token"],
                 "X-Session-Id": workspace["session_id"]},
                source=claimed,
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["source"] == "openagents:agent-alpha"


# ---------------------------------------------------------------------------
# Nothing else changed
# ---------------------------------------------------------------------------

class TestPersistenceUnchanged:
    def test_event_is_still_persisted_and_readable(self, client, db, workspace):
        """(12) SSE/persistence behaviour is untouched by the identity change."""
        resp = _post(
            client, workspace,
            {"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
            payload={"content": "persist-check"},
        )
        assert resp.status_code == 200
        event_id = resp.json()["data"]["id"]

        read = client.get("/v1/events", params={
            "network": workspace["id"],
            "channel": workspace["channel"]["name"],
            "type": "workspace.message.posted",
        }, headers={"X-Workspace-Token": workspace["token"]})
        assert read.status_code == 200
        ids = [e["id"] for e in read.json()["data"]["events"]]
        assert event_id in ids
