# -*- coding: utf-8 -*-
"""Tests for Placement AI v2.0's one-student-one-workspace model.

A user account owns at most one active workspace, resolved/created via
`app.access.get_or_create_owned_workspace` and exposed at
`GET /v1/account/workspace`. `workspaces.owner_user_id` (with the
`uq_workspace_owner_active` partial unique index) is the actual guarantee —
see app/models.py and alembic/versions/053_workspace_owner_user_id.py.

Identity-token verification is stubbed the same way as
test_workspace_membership.py (patching app.access.verify_identity_claims).
"""

import app.firebase_auth as firebase_auth
from app.access import get_or_create_owned_workspace, resolve_owned_workspace
from app.models import User, Workspace


def _claims(email, uid="uid", name="Test User"):
    return {"provider": "supabase", "email": email, "supabase_uid": uid,
            "apple_sub": None, "display_name": name}


def _stub_identity(monkeypatch, mapping):
    """Map bearer string -> claims dict (or None). Patched at the lowest
    level (`verify_supabase_claims`) so both the access-resolution path
    (via app.access.verify_identity_claims) and the 401-vs-403 disambiguation
    path (via app.firebase_auth.verify_identity_token, imported fresh inside
    _workspace_access_denied) see the same stubbed identity."""
    monkeypatch.setattr(firebase_auth, "verify_supabase_claims", lambda tok: mapping.get(tok))


def _auth(bearer):
    return {"Authorization": f"Bearer {bearer}"}


def _user_with_username(db, email, username):
    """A User row pre-seeded with `username` set, so the `/v1/account/*`
    endpoints' "username setup required" gate doesn't block these tests —
    that gate is orthogonal to what's under test here."""
    user = User(email=email, username=username)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Core guarantee, at the function level (no HTTP, no username gate involved)
# ---------------------------------------------------------------------------

class TestGetOrCreateOwnedWorkspace:
    def test_new_user_gets_exactly_one_workspace(self, db):
        user = User(email="new@x.com")
        db.add(user); db.flush()

        ws = get_or_create_owned_workspace(db, user)
        db.commit()

        assert ws.owner_user_id == user.id
        assert ws.status == "active"
        count = db.query(Workspace).filter(
            Workspace.owner_user_id == user.id, Workspace.status == "active",
        ).count()
        assert count == 1

    def test_repeated_call_returns_the_same_workspace(self, db):
        user = User(email="repeat@x.com")
        db.add(user); db.flush()

        first = get_or_create_owned_workspace(db, user)
        db.commit()
        second = get_or_create_owned_workspace(db, user)
        db.commit()

        assert first.id == second.id

    def test_resolve_returns_none_before_provisioning(self, db):
        user = User(email="fresh@x.com")
        db.add(user); db.flush(); db.commit()
        assert resolve_owned_workspace(db, user) is None

    def test_database_rejects_a_second_active_owned_workspace(self, db):
        """The actual guarantee: uq_workspace_owner_active. Bypasses the
        helper entirely to prove the constraint itself, not just the Python
        that's supposed to respect it."""
        import pytest
        from sqlalchemy.exc import IntegrityError

        user = User(email="race@x.com")
        db.add(user); db.flush()
        db.add(Workspace(slug="ws-one", name="One", owner_user_id=user.id, status="active"))
        db.commit()

        db.add(Workspace(slug="ws-two", name="Two", owner_user_id=user.id, status="active"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_archived_workspace_does_not_block_reprovisioning(self, db):
        """A soft-deleted owned workspace (e.g. after account deletion) must
        not permanently block that user from ever getting a new one."""
        user = User(email="again@x.com")
        db.add(user); db.flush()
        old = Workspace(slug="old-one", name="Old", owner_user_id=user.id, status="deleted")
        db.add(old)
        db.commit()

        ws = get_or_create_owned_workspace(db, user)
        db.commit()

        assert ws.id != old.id
        assert ws.status == "active"

    def test_provisioning_creates_pai_counselor_exactly_once(self, db, monkeypatch):
        from app import config as app_config
        monkeypatch.setattr(app_config.config, "PAI_ENABLED", True)
        monkeypatch.setattr(app_config.config, "PAI_API_KEY", "test-key")

        from app.models import WorkspaceMember
        user = User(email="student@x.com")
        db.add(user); db.flush()

        ws = get_or_create_owned_workspace(db, user)
        db.commit()

        members = db.query(WorkspaceMember).filter(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.agent_name == "pai",
        ).all()
        assert len(members) == 1

        # Calling again must not add a second one.
        get_or_create_owned_workspace(db, user)
        db.commit()
        members_again = db.query(WorkspaceMember).filter(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.agent_name == "pai",
        ).all()
        assert len(members_again) == 1

    def test_provisioning_creates_canonical_conversation_exactly_once(self, db, monkeypatch):
        from app import config as app_config
        monkeypatch.setattr(app_config.config, "PAI_ENABLED", True)
        monkeypatch.setattr(app_config.config, "PAI_API_KEY", "test-key")

        from app.models import Channel
        from app.services.pai import PAI_PRIMARY_CHANNEL
        user = User(email="student2@x.com")
        db.add(user); db.flush()

        ws = get_or_create_owned_workspace(db, user)
        db.commit()

        channels = db.query(Channel).filter(
            Channel.workspace_id == ws.id, Channel.name == PAI_PRIMARY_CHANNEL,
        ).all()
        assert len(channels) == 1


# ---------------------------------------------------------------------------
# Migration-shaped scenarios: simulate what alembic 053's backfill does
# (set owner_user_id on exactly one of several legacy-owned workspaces) and
# verify the new resolution path behaves correctly against that end state.
# ---------------------------------------------------------------------------

class TestLegacyBackfillShape:
    def test_single_legacy_workspace_resolves_as_canonical(self, db):
        user = User(email="single-legacy@x.com")
        db.add(user); db.flush()
        ws = Workspace(slug="legacy1", name="Legacy", creator_email=user.email, owner_user_id=user.id, status="active")
        db.add(ws)
        db.commit()

        resolved = resolve_owned_workspace(db, user)
        assert resolved.id == ws.id

    def test_multiple_legacy_workspaces_leave_only_one_owned(self, db):
        """Mirrors migration 053: of several old workspaces this user
        created, only the canonical pick gets owner_user_id — the rest are
        untouched (not deleted, not merged, just unreachable via ownership)."""
        user = User(email="multi-legacy@x.com")
        db.add(user); db.flush()

        canonical = Workspace(slug="canon", name="Canonical", creator_email=user.email, owner_user_id=user.id, status="active")
        extra_a = Workspace(slug="extra-a", name="Extra A", creator_email=user.email, status="active")
        extra_b = Workspace(slug="extra-b", name="Extra B", creator_email=user.email, status="active")
        db.add_all([canonical, extra_a, extra_b])
        db.commit()

        resolved = resolve_owned_workspace(db, user)
        assert resolved.id == canonical.id
        # The extras still exist — their data was not deleted or merged.
        assert db.query(Workspace).filter(Workspace.creator_email == user.email).count() == 3
        # get_or_create must not create a fourth workspace when one is owned.
        again = get_or_create_owned_workspace(db, user)
        db.commit()
        assert again.id == canonical.id
        assert db.query(Workspace).filter(Workspace.owner_user_id == user.id).count() == 1


# ---------------------------------------------------------------------------
# HTTP surface: GET /v1/account/workspace
# ---------------------------------------------------------------------------

class TestAccountWorkspaceEndpoint:
    def test_new_user_receives_one_workspace(self, client, db, monkeypatch):
        _user_with_username(db, "http-new@x.com", "httpnew")
        _stub_identity(monkeypatch, {"tok": _claims("http-new@x.com")})

        r = client.get("/v1/account/workspace", headers=_auth("tok"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["name"]
        assert data["slug"]
        assert data["workspaceId"]

    def test_repeated_get_returns_same_workspace(self, client, db, monkeypatch):
        _user_with_username(db, "http-repeat@x.com", "httprepeat")
        _stub_identity(monkeypatch, {"tok": _claims("http-repeat@x.com")})

        first = client.get("/v1/account/workspace", headers=_auth("tok")).json()["data"]
        second = client.get("/v1/account/workspace", headers=_auth("tok")).json()["data"]
        assert first["workspaceId"] == second["workspaceId"]

    def test_no_create_another_workspace_path(self, client, db, monkeypatch):
        """POST /v1/workspaces from an authenticated student never creates a
        second workspace — it returns the existing canonical one."""
        _user_with_username(db, "http-post@x.com", "httppost")
        _stub_identity(monkeypatch, {"tok": _claims("http-post@x.com")})

        first = client.get("/v1/account/workspace", headers=_auth("tok")).json()["data"]
        r = client.post("/v1/workspaces", json={"name": "Another One"}, headers=_auth("tok"))
        assert r.status_code == 200
        assert r.json()["data"]["workspaceId"] == first["workspaceId"]

        count = db.query(Workspace).filter(
            Workspace.owner_user_id == db.query(User.id).filter(User.email == "http-post@x.com").scalar(),
        ).count()
        assert count == 1

    def test_user_a_cannot_access_user_b_workspace(self, client, db, monkeypatch):
        _user_with_username(db, "student-a@x.com", "studenta")
        _user_with_username(db, "student-b@x.com", "studentb")
        _stub_identity(monkeypatch, {
            "a": _claims("student-a@x.com"),
            "b": _claims("student-b@x.com"),
        })

        ws_a = client.get("/v1/account/workspace", headers=_auth("a")).json()["data"]

        resp = client.get(f"/v1/workspaces/{ws_a['workspaceId']}", headers=_auth("b"))
        assert resp.status_code == 403

    def test_invalid_identity_unauthorized(self, client, monkeypatch):
        _stub_identity(monkeypatch, {})
        r = client.get("/v1/account/workspace", headers=_auth("garbage"))
        assert r.status_code == 401

    def test_web_and_desktop_style_calls_resolve_same_workspace(self, client, db, monkeypatch):
        """Two separate calls with the same Supabase identity (standing in
        for web + desktop, or two tabs) must resolve to the same workspace —
        the concurrency guarantee that matters end-to-end, not just at the
        DB layer."""
        _user_with_username(db, "cross-client@x.com", "crossclient")
        _stub_identity(monkeypatch, {"tok": _claims("cross-client@x.com")})

        web = client.get("/v1/account/workspace", headers=_auth("tok")).json()["data"]
        desktop = client.get("/v1/account/workspace", headers=_auth("tok")).json()["data"]
        assert web["workspaceId"] == desktop["workspaceId"]
