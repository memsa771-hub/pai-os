# -*- coding: utf-8 -*-
"""Every student workspace is owned from the moment it exists.

The permanent invariant: an active workspace always has `owner_user_id`. There
is no anonymous creation and no claim step, so there is no window in which a
workspace exists without a human who owns it — the state that used to be
reachable, and that nothing could reach afterwards except the machine token.

These tests are deliberately written against the HTTP surface rather than the
helpers: the claim is about what an *API caller* can produce, so exercising
`provision_workspace` directly would prove nothing about the endpoints.
"""

import pytest
from sqlalchemy import select

import app.access as access
from app.models import Workspace


def _claims(email):
    return {"provider": "supabase", "email": email, "supabase_uid": "uid",
            "apple_sub": None, "display_name": "Student"}


def _as(monkeypatch, bearer="tok", email="student@example.com"):
    monkeypatch.setattr(access, "verify_identity_claims",
                        lambda t: _claims(email) if t == bearer else None)
    return {"Authorization": f"Bearer {bearer}"}


def _active_ownerless(db):
    return db.execute(
        select(Workspace).where(
            Workspace.owner_user_id.is_(None),
            Workspace.status == "active",
        )
    ).scalars().all()


class TestNoApiCanCreateAnOwnerlessWorkspace:
    """The headline invariant, from every angle a caller has."""

    def test_anonymous_create_is_refused(self, client, db):
        assert client.post("/v1/workspaces", json={"name": "Orphan"}).status_code == 401
        assert _active_ownerless(db) == []

    def test_forged_bearer_create_is_refused(self, client, db, monkeypatch):
        _as(monkeypatch)
        resp = client.post(
            "/v1/workspaces", json={"name": "Orphan"},
            headers={"Authorization": "Bearer forged"},
        )
        assert resp.status_code == 401
        assert _active_ownerless(db) == []

    def test_legacy_body_fields_cannot_smuggle_an_orphan(self, client, db):
        """`agent_name`/`creator_email` used to route to the anonymous branch."""
        resp = client.post("/v1/workspaces", json={
            "name": "Orphan",
            "agent_name": "agent-alpha",
            "creator_email": "someone@example.com",
        })
        assert resp.status_code == 401
        assert _active_ownerless(db) == []

    def test_authenticated_create_always_sets_an_owner(self, client, db, monkeypatch):
        headers = _as(monkeypatch)
        data = client.post("/v1/workspaces", json={"name": "Mine"}, headers=headers).json()["data"]

        db.rollback()
        ws = db.get(Workspace, data["workspaceId"])
        assert ws.owner_user_id is not None
        assert ws.status == "active"
        assert _active_ownerless(db) == []

    def test_account_endpoint_always_sets_an_owner(self, client, db, monkeypatch):
        headers = _as(monkeypatch, email="acct@example.com")
        from app.models import User
        db.add(User(email="acct@example.com", username="acct"))
        db.commit()

        data = client.get("/v1/account/workspace", headers=headers).json()["data"]
        db.rollback()
        ws = db.get(Workspace, data["workspaceId"])
        assert ws.owner_user_id is not None
        assert _active_ownerless(db) == []

    def test_the_suite_never_leaves_an_ownerless_active_workspace(self, client, db, monkeypatch):
        """Both creation entry points, then a sweep of the whole table."""
        headers = _as(monkeypatch, email="sweep@example.com")
        client.post("/v1/workspaces", json={"name": "A"}, headers=headers)
        client.post("/v1/workspaces", json={"name": "B"}, headers=headers)
        db.rollback()
        assert _active_ownerless(db) == []


class TestClaimIsGone:
    """"Create first, claim later" no longer exists as a lifecycle."""

    def test_claim_endpoint_is_removed(self, client, workspace, monkeypatch):
        _as(monkeypatch)
        resp = client.post(
            f"/v1/workspaces/{workspace['id']}/claim",
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 404

    def test_no_claim_handler_remains(self):
        import app.routers.workspaces as workspaces_router
        assert not hasattr(workspaces_router, "claim_workspace")

    def test_workspace_has_no_creator_email_column(self):
        """It was the claim era's identity field, and an unauthenticated
        `GET /v1/workspaces?creator_email=` filtered on it."""
        assert not hasattr(Workspace, "creator_email")


class TestOwnershipIsStillSingular:
    """Removing the anonymous path must not weaken the one-workspace rule."""

    def test_repeated_creation_yields_one_workspace(self, client, db, monkeypatch):
        headers = _as(monkeypatch, email="repeat@example.com")
        ids = {
            client.post("/v1/workspaces", json={"name": f"n{i}"},
                        headers=headers).json()["data"]["workspaceId"]
            for i in range(3)
        }
        assert len(ids) == 1

        db.rollback()
        owned = db.execute(
            select(Workspace).where(Workspace.status == "active")
        ).scalars().all()
        assert len([w for w in owned if w.owner_user_id is not None]) == 1

    def test_agents_still_reach_the_workspace_by_machine_token(self, client, monkeypatch):
        """The refactor must not cut PAI Counselor/Operator off."""
        headers = _as(monkeypatch, email="agentcheck@example.com")
        data = client.post("/v1/workspaces", json={"name": "WS"}, headers=headers).json()["data"]
        resp = client.get(
            f"/v1/workspaces/{data['workspaceId']}",
            headers={"X-Workspace-Token": data["token"]},
        )
        assert resp.status_code == 200
