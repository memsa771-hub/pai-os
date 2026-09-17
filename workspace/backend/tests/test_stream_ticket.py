# -*- coding: utf-8 -*-
"""The browser never holds the workspace machine token.

`GET /v1/account/workspace` used to hand `workspace.password_hash` — the
credential PAI's agents WRITE with — to the browser, which parked it in a
30-day JS-readable cookie and put it in every SSE and <img> URL. These tests
pin the replacement: the response carries no token at all, and the only URL
credential the server accepts is a short-lived, read-only ticket.
"""

import pytest
from sqlalchemy import select

import app.firebase_auth as firebase_auth
from app import stream_ticket
from app.models import User, Workspace
from tests.conftest import make_owned_workspace


@pytest.fixture
def owned(client, db):
    data = make_owned_workspace()
    ws = db.execute(
        select(Workspace).where(Workspace.id == data["workspaceId"])
    ).scalar_one()
    return data, ws


@pytest.fixture
def authed_headers(db, monkeypatch):
    """A signed-in student. Identity verification is stubbed at the lowest
    level, as in test_canonical_workspace.py."""
    email = "ticket-owner@x.com"
    db.add(User(email=email, username="ticketowner"))
    db.commit()
    claims = {"provider": "supabase", "email": email, "supabase_uid": "uid",
              "apple_sub": None, "display_name": "Ticket Owner"}
    monkeypatch.setattr(firebase_auth, "verify_supabase_claims",
                        lambda tok: claims if tok == "tok" else None)
    return {"Authorization": "Bearer tok"}


# ---------------------------------------------------------------------------
# The ticket primitive
# ---------------------------------------------------------------------------

class TestTicketPrimitive:
    def test_roundtrip(self, owned):
        _, ws = owned
        assert stream_ticket.verify(ws, stream_ticket.mint(ws, "user-1"))

    def test_never_contains_the_machine_token(self, owned):
        _, ws = owned
        assert ws.password_hash not in stream_ticket.mint(ws, "user-1")

    def test_expired_ticket_rejected(self, owned):
        _, ws = owned
        assert not stream_ticket.verify(ws, stream_ticket.mint(ws, "u", ttl_seconds=-1))

    def test_tampered_payload_rejected(self, owned):
        _, ws = owned
        ticket = stream_ticket.mint(ws, "user-1")
        payload, _, signature = ticket.partition(".")
        # Same signature, different payload.
        assert not stream_ticket.verify(ws, f"{payload[:-2]}xy.{signature}")

    def test_ticket_for_another_workspace_rejected(self, db):
        a = db.execute(select(Workspace).where(Workspace.id == make_owned_workspace(
            email="a@x.com")["workspaceId"])).scalar_one()
        b = db.execute(select(Workspace).where(Workspace.id == make_owned_workspace(
            email="b@x.com", agent_name="agent-b")["workspaceId"])).scalar_one()
        assert not stream_ticket.verify(b, stream_ticket.mint(a, "user-1"))

    def test_rotating_the_workspace_token_revokes_tickets(self, owned, db):
        _, ws = owned
        ticket = stream_ticket.mint(ws, "user-1")
        ws.password_hash = "rotated-" + ws.password_hash
        assert not stream_ticket.verify(ws, ticket)

    def test_garbage_is_not_a_ticket(self, owned):
        _, ws = owned
        for junk in (None, "", ".", "nope", "a.b", "!!!.???"):
            assert not stream_ticket.verify(ws, junk)


# ---------------------------------------------------------------------------
# The machine token is no longer handed out
# ---------------------------------------------------------------------------

class TestTokenNotExposed:
    def test_account_workspace_returns_no_token(self, client, authed_headers, db):
        resp = client.get("/v1/account/workspace", headers=authed_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "token" not in data
        assert data["streamTicket"]

        ws = db.execute(select(Workspace).where(
            Workspace.id == data["workspaceId"])).scalar_one()
        assert ws.password_hash not in resp.text

    def test_stream_ticket_endpoint_requires_identity(self, client):
        assert client.post("/v1/account/stream-ticket").status_code == 401

    def test_stream_ticket_endpoint_mints_a_usable_ticket(
        self, client, authed_headers, db
    ):
        ws_id = client.get("/v1/account/workspace",
                           headers=authed_headers).json()["data"]["workspaceId"]
        resp = client.post("/v1/account/stream-ticket", headers=authed_headers)
        assert resp.status_code == 200
        ticket = resp.json()["data"]["streamTicket"]

        ws = db.execute(select(Workspace).where(Workspace.id == ws_id)).scalar_one()
        assert stream_ticket.verify(ws, ticket)
        assert ws.password_hash not in resp.text


# ---------------------------------------------------------------------------
# What the ticket can and cannot open
# ---------------------------------------------------------------------------

@pytest.fixture
def sse_sees_test_db(monkeypatch):
    """The SSE handler opens its own session rather than Depends(get_db) — it
    must not pin a pooled connection for the life of a stream — so the usual
    dependency override doesn't reach it."""
    import app.routers.events as events_module
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(events_module, "SessionLocal", TestingSessionLocal)


class TestTicketScope:
    def test_sse_rejects_the_old_token_query_param(self, client, owned, sse_sees_test_db):
        data, ws = owned
        resp = client.get("/v1/events/stream", params={
            "network": data["workspaceId"], "token": ws.password_hash,
        })
        assert resp.status_code == 401

    def test_sse_rejects_a_bad_ticket(self, client, owned, sse_sees_test_db):
        data, _ = owned
        resp = client.get("/v1/events/stream", params={
            "network": data["workspaceId"], "ticket": "not-a-ticket",
        })
        assert resp.status_code == 401

    def test_file_download_rejects_the_old_token_query_param(self, client, owned):
        data, ws = owned
        file_id = _upload(client, data)
        resp = client.get(f"/v1/files/{file_id}", params={"token": ws.password_hash})
        assert resp.status_code == 401

    def test_file_download_accepts_a_ticket(self, client, owned):
        data, ws = owned
        file_id = _upload(client, data)
        ticket = stream_ticket.mint(ws, "user-1")
        resp = client.get(f"/v1/files/{file_id}", params={"ticket": ticket})
        assert resp.status_code == 200
        assert resp.content == b"hello"

    def test_ticket_cannot_write(self, client, owned):
        """Read-only by construction: no mutating route reads `ticket`."""
        data, ws = owned
        ticket = stream_ticket.mint(ws, "user-1")
        resp = client.post(
            "/v1/knowledge",
            params={"ticket": ticket},
            json={"network": data["workspaceId"], "title": "T", "content": "C"},
        )
        assert resp.status_code == 401


def _upload(client, data):
    resp = client.post(
        "/v1/files",
        data={"network": data["workspaceId"]},
        files={"file": ("a.txt", b"hello", "text/plain")},
        headers={"X-Workspace-Token": data["token"],
                 "X-Session-Id": data["sessionId"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]
