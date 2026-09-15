# -*- coding: utf-8 -*-
"""Tests for enforced-login v1.0: users, memberships, access rules,
reconciliation and auto-provision (Phase 1).

Identity-token verification is stubbed (no real Supabase/Apple) by patching
app.access.verify_identity_claims, which every caller routes through.
"""

import asyncio
from types import SimpleNamespace

import app.access as access
from app.access import (
    get_or_create_user,
    provision_workspace,
    reconcile_memberships,
    verify_workspace_access,
)
from app.mods.auth import AuthMod
from app.models import User, Workspace, WorkspaceCollaborator, WorkspaceMembership


def _claims(email, uid="uid", name="Test User"):
    return {"provider": "supabase", "email": email, "supabase_uid": uid,
            "apple_sub": None, "display_name": name}


def _stub_identity(monkeypatch, mapping):
    """Map bearer string -> claims dict (or None)."""
    monkeypatch.setattr(access, "verify_identity_claims", lambda tok: mapping.get(tok))


# ---------------------------------------------------------------------------
# verify_workspace_access — the single access check
# ---------------------------------------------------------------------------

class TestAccessRules:
    def test_token_match_allows(self, db):
        ws = Workspace(name="W", slug="s1", password_hash="tok")
        db.add(ws); db.commit()
        assert verify_workspace_access(ws, "tok", None) is True

    def test_wrong_token_no_identity_denied(self, db):
        ws = Workspace(name="W", slug="s2", password_hash="tok")
        db.add(ws); db.commit()
        assert verify_workspace_access(ws, "nope", None) is False

    def test_open_workspace_grandfathered(self, db):
        ws = Workspace(name="W", slug="s3", password_hash=None, require_login=False)
        db.add(ws); db.commit()
        assert verify_workspace_access(ws, None, None) is True

    def test_open_workspace_require_login_denies_anonymous(self, db):
        ws = Workspace(name="W", slug="s4", password_hash=None, require_login=True)
        db.add(ws); db.commit()
        assert verify_workspace_access(ws, None, None) is False

    def test_member_identity_allows(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"bob": _claims("bob@x.com")})
        ws = Workspace(name="W", slug="s5", password_hash="tok", require_login=True)
        db.add(ws); db.flush()
        u = User(email="bob@x.com"); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role="member"))
        db.commit()
        assert verify_workspace_access(ws, None, "Bearer bob") is True

    def test_legacy_creator_email_fallback_allows(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("alice@x.com")})
        ws = Workspace(name="W", slug="s6", password_hash="tok", creator_email="alice@x.com")
        db.add(ws); db.commit()
        # No membership row yet — legacy owner email still grants access.
        assert verify_workspace_access(ws, None, "Bearer al") is True

    def test_min_role_enforced(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"m": _claims("m@x.com")})
        ws = Workspace(name="W", slug="s7", password_hash="tok", require_login=True)
        db.add(ws); db.flush()
        u = User(email="m@x.com"); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role="member"))
        db.commit()
        assert verify_workspace_access(ws, None, "Bearer m", min_role="member") is True
        assert verify_workspace_access(ws, None, "Bearer m", min_role="admin") is False

    def test_token_bypasses_min_role(self, db):
        ws = Workspace(name="W", slug="s8", password_hash="tok", require_login=True)
        db.add(ws); db.commit()
        assert verify_workspace_access(ws, "tok", None, min_role="owner") is True


# ---------------------------------------------------------------------------
# Reconciliation & provisioning
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_creator_email_becomes_owner(self, db):
        ws = Workspace(name="W", slug="r1", password_hash="tok", creator_email="alice@x.com")
        db.add(ws); db.commit()
        u = get_or_create_user(db, _claims("alice@x.com"))
        reconcile_memberships(db, u); db.commit()
        m = db.query(WorkspaceMembership).filter_by(workspace_id=ws.id, user_id=u.id).one()
        assert m.role == "owner"

    def test_collaborator_roles_map(self, db):
        ws1 = Workspace(name="W1", slug="r2", password_hash="t1")
        ws2 = Workspace(name="W2", slug="r3", password_hash="t2")
        db.add_all([ws1, ws2]); db.flush()
        db.add(WorkspaceCollaborator(workspace_id=ws1.id, email="c@x.com", role="editor"))
        db.add(WorkspaceCollaborator(workspace_id=ws2.id, email="c@x.com", role="viewer"))
        db.commit()
        u = get_or_create_user(db, _claims("c@x.com"))
        reconcile_memberships(db, u); db.commit()
        roles = {m.workspace_id: m.role for m in
                 db.query(WorkspaceMembership).filter_by(user_id=u.id).all()}
        assert roles[ws1.id] == "member"
        assert roles[ws2.id] == "viewer"

    def test_reconcile_does_not_downgrade_existing(self, db):
        ws = Workspace(name="W", slug="r4", password_hash="tok", creator_email="a@x.com")
        db.add(ws); db.flush()
        u = get_or_create_user(db, _claims("a@x.com"))
        # Pre-existing admin membership must survive an owner reconcile pass...
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role="admin"))
        db.commit()
        reconcile_memberships(db, u); db.commit()
        m = db.query(WorkspaceMembership).filter_by(workspace_id=ws.id, user_id=u.id).one()
        assert m.role == "admin"

    def test_get_or_create_user_idempotent(self, db):
        u1 = get_or_create_user(db, _claims("dup@x.com", uid="a")); db.commit()
        u2 = get_or_create_user(db, _claims("dup@x.com", uid="a")); db.commit()
        assert u1.id == u2.id
        assert db.query(User).filter_by(email="dup@x.com").count() == 1

    def test_provision_workspace_owner(self, db):
        u = get_or_create_user(db, _claims("new@x.com")); db.commit()
        ws = provision_workspace(db, u); db.commit()
        assert ws.creator_email == "new@x.com"
        assert ws.password_hash  # token present for agent/legacy access
        m = db.query(WorkspaceMembership).filter_by(workspace_id=ws.id, user_id=u.id).one()
        assert m.role == "owner"


# ---------------------------------------------------------------------------
# GET /v1/account/workspaces — Membership Home endpoint
# ---------------------------------------------------------------------------

class TestAccountWorkspacesEndpoint:
    def test_new_user_auto_provisioned(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"carol": _claims("carol@x.com")})
        r = client.get("/v1/account/workspaces", headers={"Authorization": "Bearer carol"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["role"] == "owner"
        assert data[0]["name"] == "My Workspace"

    def test_existing_creator_reconciled_not_provisioned(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"dave": _claims("dave@x.com")})
        ws = Workspace(name="Dave WS", slug="acc1", password_hash="tok", creator_email="dave@x.com")
        db.add(ws); db.commit()
        r = client.get("/v1/account/workspaces", headers={"Authorization": "Bearer dave"})
        data = r.json()["data"]
        assert len(data) == 1  # reconciled the existing one, did NOT auto-create
        assert data[0]["slug"] == "acc1"
        assert data[0]["role"] == "owner"

    def test_invalid_identity_unauthorized(self, client, monkeypatch):
        _stub_identity(monkeypatch, {})  # any bearer -> None
        r = client.get("/v1/account/workspaces", headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Phase 3 — require_login default/toggle + team management
# ---------------------------------------------------------------------------

def _auth(bearer):
    return {"Authorization": f"Bearer {bearer}"}


class TestRequireLoginDefault:
    def test_identity_created_workspace_enforces_login(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        r = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al"))
        wid = r.json()["data"]["workspaceId"]
        detail = client.get(f"/v1/workspaces/{wid}", headers=_auth("al")).json()["data"]
        assert detail["requireLogin"] is True

    def test_anonymous_created_workspace_enforces_login_too(self, client):
        # Secure by default: even CLI/anonymous creation starts with
        # require_login on. Token (machine) access still works throughout.
        r = client.post("/v1/workspaces", json={"name": "WS", "creator_email": "a@x.com"})
        data = r.json()["data"]
        detail = client.get(
            f"/v1/workspaces/{data['workspaceId']}",
            headers={"X-Workspace-Token": data["token"]},
        ).json()["data"]
        assert detail["requireLogin"] is True


class TestRequireLoginToggle:
    def test_owner_can_toggle(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al")).json()["data"]["workspaceId"]
        r = client.patch(f"/v1/workspaces/{wid}", json={"require_login": False}, headers=_auth("al"))
        assert r.status_code == 200
        assert r.json()["data"]["requireLogin"] is False

    def test_member_cannot_toggle(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com"), "bob": _claims("bob@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al")).json()["data"]["workspaceId"]
        u = User(email="bob@x.com"); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=wid, user_id=u.id, role="member"))
        db.commit()
        r = client.patch(f"/v1/workspaces/{wid}", json={"require_login": False}, headers=_auth("bob"))
        assert r.status_code == 403


class TestProfile:
    """GET/PATCH /v1/account/profile — the signed-in user's name + avatar."""

    def test_get_and_update_profile(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        p = client.get("/v1/account/profile", headers=_auth("al")).json()["data"]
        assert p == {
            "email": "al@x.com", "displayName": "Test User",
            "avatarUrl": None, "welcomeSeen": False,
        }

        r = client.patch(
            "/v1/account/profile",
            json={"display_name": "Ada L.", "avatar_url": "data:image/jpeg;base64,abc123"},
            headers=_auth("al"),
        )
        assert r.status_code == 200
        assert r.json()["data"] == {
            "email": "al@x.com", "displayName": "Ada L.",
            "avatarUrl": "data:image/jpeg;base64,abc123", "welcomeSeen": False,
        }

        # Empty string clears the avatar; omitted fields stay untouched.
        r = client.patch("/v1/account/profile", json={"avatar_url": ""}, headers=_auth("al"))
        assert r.json()["data"] == {
            "email": "al@x.com", "displayName": "Ada L.",
            "avatarUrl": None, "welcomeSeen": False,
        }

        # welcomeSeen (camelCase wire name) persists; other fields untouched.
        r = client.patch("/v1/account/profile", json={"welcomeSeen": True}, headers=_auth("al"))
        assert r.json()["data"] == {
            "email": "al@x.com", "displayName": "Ada L.",
            "avatarUrl": None, "welcomeSeen": True,
        }
        p = client.get("/v1/account/profile", headers=_auth("al")).json()["data"]
        assert p["welcomeSeen"] is True

    def test_profile_validation(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        assert client.get("/v1/account/profile").status_code == 401
        assert client.patch(
            "/v1/account/profile", json={"display_name": "   "}, headers=_auth("al"),
        ).status_code == 400
        assert client.patch(
            "/v1/account/profile", json={"avatar_url": "javascript:alert(1)"}, headers=_auth("al"),
        ).status_code == 400
        assert client.patch(
            "/v1/account/profile",
            json={"avatar_url": "data:image/png;base64," + "A" * 300_000},
            headers=_auth("al"),
        ).status_code == 400


class TestMeEndpoint:
    """GET /v1/workspaces/{id}/me — the caller's identity + effective role,
    used by the settings dashboard to gate admin UI client-side."""

    def test_owner_identity(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al")).json()["data"]["workspaceId"]
        me = client.get(f"/v1/workspaces/{wid}/me", headers=_auth("al")).json()["data"]
        assert me["email"] == "al@x.com"
        assert me["authenticated"] is True
        assert me["role"] == "owner"
        assert me["effectiveRole"] == "owner"
        assert me["tokenAccess"] is False

    def test_viewer_effective_role_is_viewer(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com"), "v": _claims("v@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al")).json()["data"]["workspaceId"]
        u = User(email="v@x.com"); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=wid, user_id=u.id, role="viewer"))
        db.commit()
        me = client.get(f"/v1/workspaces/{wid}/me", headers=_auth("v")).json()["data"]
        assert me["role"] == "viewer"
        assert me["effectiveRole"] == "viewer"

    def test_token_access_is_owner_equivalent(self, client):
        data = client.post("/v1/workspaces", json={"name": "WS"}).json()["data"]
        me = client.get(
            f"/v1/workspaces/{data['workspaceId']}/me",
            headers={"X-Workspace-Token": data["token"]},
        ).json()["data"]
        assert me["authenticated"] is False
        assert me["role"] is None
        assert me["tokenAccess"] is True
        assert me["effectiveRole"] == "owner"

    def test_anonymous_denied_on_enforced_workspace(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"}, headers=_auth("al")).json()["data"]["workspaceId"]
        assert client.get(f"/v1/workspaces/{wid}/me").status_code == 401


# ---------------------------------------------------------------------------
# Phase 4 — viewer read-only enforcement
# ---------------------------------------------------------------------------

class TestViewerEnforcement:
    """AuthMod (the event write path) enforces min_role=member, so viewers
    can't post/interact. Exercised directly to avoid the DB-backed pipeline."""

    def _process(self, db, ws, token=None, bearer=None):
        event = SimpleNamespace(network=None)
        ctx = SimpleNamespace(
            extra={"workspace": ws, "token": token, "bearer_token": bearer},
            db=db,
        )
        return asyncio.run(AuthMod().process(event, ctx))

    def _ws_with_member(self, db, slug, email, role):
        ws = Workspace(name="W", slug=slug, password_hash="tok", require_login=True)
        db.add(ws); db.flush()
        u = User(email=email); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role=role))
        db.commit()
        return ws

    def test_viewer_cannot_post(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"v": _claims("v@x.com")})
        ws = self._ws_with_member(db, "ve1", "v@x.com", "viewer")
        assert self._process(db, ws, bearer="v") is None

    def test_member_can_post(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"m": _claims("m@x.com")})
        ws = self._ws_with_member(db, "ve2", "m@x.com", "member")
        assert self._process(db, ws, bearer="m") is not None

    def test_agent_token_bypasses_role(self, db):
        ws = Workspace(name="W", slug="ve3", password_hash="tok", require_login=True)
        db.add(ws); db.commit()
        assert self._process(db, ws, token="tok") is not None

    def test_anonymous_allowed_on_open_workspace(self, db):
        ws = Workspace(name="W", slug="ve4", password_hash=None, require_login=False)
        db.add(ws); db.commit()
        assert self._process(db, ws) is not None


class TestViewerToken:
    def test_viewer_gets_null_token_owner_gets_token(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com"), "vv": _claims("vv@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "W"}, headers=_auth("al")).json()["data"]["workspaceId"]
        u = User(email="vv@x.com"); db.add(u); db.flush()
        db.add(WorkspaceMembership(workspace_id=wid, user_id=u.id, role="viewer"))
        db.commit()

        vv = [w for w in client.get("/v1/account/workspaces", headers=_auth("vv")).json()["data"] if w["workspaceId"] == wid][0]
        assert vv["role"] == "viewer" and vv["token"] is None

        al = [w for w in client.get("/v1/account/workspaces", headers=_auth("al")).json()["data"] if w["workspaceId"] == wid][0]
        assert al["role"] == "owner" and al["token"] is not None
