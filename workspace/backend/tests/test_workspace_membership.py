# -*- coding: utf-8 -*-
"""One student, one personal workspace — the whole human authorization model.

A human may touch a workspace iff `user.id == workspace.owner_user_id`. There
is no membership table, no role hierarchy, no invitation and no email fallback.
Agents keep their own credential: the workspace machine token.

Identity verification is stubbed (no real Supabase/Apple) by patching
app.access.verify_identity_claims, which every caller routes through.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

import app.access as access
from app.access import (
    get_or_create_owned_workspace,
    get_or_create_user,
    is_workspace_owner,
    provision_workspace,
    verify_workspace_access,
)
from app.mods.auth import AuthMod
from app.models import User, Workspace


def _claims(email, uid="uid", name="Test User"):
    return {"provider": "supabase", "email": email, "supabase_uid": uid,
            "apple_sub": None, "display_name": name}


def _stub_identity(monkeypatch, mapping):
    """Map bearer string -> claims dict (or None).

    Two entry points need stubbing: `access.verify_identity_claims` (the access
    check) and `firebase_auth.verify_identity_token` (used by the router to tell
    "bad credentials" 401 from "valid user, not yours" 403).
    """
    import app.firebase_auth as firebase_auth
    monkeypatch.setattr(access, "verify_identity_claims", lambda tok: mapping.get(tok))
    monkeypatch.setattr(
        firebase_auth, "verify_identity_token",
        lambda tok: (mapping.get(tok) or {}).get("email"),
    )


def _auth(bearer):
    return {"Authorization": f"Bearer {bearer}"}


def _seed_user(db, email, username):
    """A User with `username` set, so the /v1/account/* "username setup
    required" gate doesn't stand in for the access check being tested."""
    user = User(email=email, username=username)
    db.add(user)
    db.commit()
    return user


def _owned_workspace(db, email, slug, token="tok"):
    """A user plus the one workspace they own."""
    user = User(email=email)
    db.add(user)
    db.flush()
    ws = Workspace(name="W", slug=slug, password_hash=token,
                   owner_user_id=user.id, require_login=True, status="active")
    db.add(ws)
    db.commit()
    return user, ws


# ---------------------------------------------------------------------------
# verify_workspace_access — machine token, or the owner. Nothing else.
# ---------------------------------------------------------------------------

class TestAccessRules:
    def test_machine_token_allows(self, db):
        _, ws = _owned_workspace(db, "a@x.com", "acc1")
        assert verify_workspace_access(ws, "tok", None, db=db) is True

    def test_wrong_token_denied(self, db):
        _, ws = _owned_workspace(db, "a@x.com", "acc2")
        assert verify_workspace_access(ws, "nope", None, db=db) is False

    def test_owner_identity_allows(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"a": _claims("a@x.com")})
        _, ws = _owned_workspace(db, "a@x.com", "acc3")
        assert verify_workspace_access(ws, None, "Bearer a", db=db) is True

    def test_anonymous_denied(self, db):
        """No token, no bearer — denied, even with require_login False."""
        _, ws = _owned_workspace(db, "a@x.com", "acc4")
        ws.require_login = False
        db.commit()
        assert verify_workspace_access(ws, None, None, db=db) is False

    def test_tokenless_workspace_is_not_open(self, db):
        """The old 'no token + no require_login = public' grandfather is gone."""
        _, ws = _owned_workspace(db, "a@x.com", "acc5", token=None)
        ws.require_login = False
        db.commit()
        assert verify_workspace_access(ws, None, None, db=db) is False

    def test_invalid_bearer_denied(self, db, monkeypatch):
        _stub_identity(monkeypatch, {})          # every bearer fails to verify
        _, ws = _owned_workspace(db, "a@x.com", "acc6")
        assert verify_workspace_access(ws, None, "Bearer forged", db=db) is False

    def test_unowned_workspace_has_no_human(self, db, monkeypatch):
        """A workspace with no owner is reachable only by its machine token."""
        _stub_identity(monkeypatch, {"a": _claims("a@x.com")})
        db.add(User(email="a@x.com"))
        ws = Workspace(name="W", slug="acc7", password_hash="tok", owner_user_id=None)
        db.add(ws)
        db.commit()
        assert verify_workspace_access(ws, None, "Bearer a", db=db) is False
        assert verify_workspace_access(ws, "tok", None, db=db) is True


# ---------------------------------------------------------------------------
# Tenant isolation — the property the whole refactor exists to guarantee
# ---------------------------------------------------------------------------

class TestTenantIsolation:
    def test_user_a_cannot_access_workspace_b(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"a": _claims("a@x.com"), "b": _claims("b@x.com")})
        _, ws_a = _owned_workspace(db, "a@x.com", "iso1", token="tok-a")
        _, ws_b = _owned_workspace(db, "b@x.com", "iso2", token="tok-b")

        assert verify_workspace_access(ws_a, None, "Bearer a", db=db) is True
        assert verify_workspace_access(ws_b, None, "Bearer b", db=db) is True
        # The crossed pairs are the point.
        assert verify_workspace_access(ws_b, None, "Bearer a", db=db) is False
        assert verify_workspace_access(ws_a, None, "Bearer b", db=db) is False

    def test_other_users_token_does_not_unlock_this_workspace(self, db):
        _, ws_a = _owned_workspace(db, "a@x.com", "iso3", token="tok-a")
        _, _ws_b = _owned_workspace(db, "b@x.com", "iso4", token="tok-b")
        assert verify_workspace_access(ws_a, "tok-b", None, db=db) is False

    def test_a_logged_in_stranger_is_not_an_owner(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"c": _claims("c@x.com")})
        db.add(User(email="c@x.com"))
        _, ws = _owned_workspace(db, "a@x.com", "iso5")
        db.commit()
        assert is_workspace_owner(db, ws, "Bearer c") is False

    def test_http_layer_denies_cross_tenant_read(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"a": _claims("a@x.com"), "b": _claims("b@x.com")})
        _seed_user(db, "a@x.com", "usera")
        _seed_user(db, "b@x.com", "userb")
        wid_a = client.post("/v1/workspaces", json={"name": "A"},
                            headers=_auth("a")).json()["data"]["workspaceId"]
        assert client.get(f"/v1/workspaces/{wid_a}", headers=_auth("a")).status_code == 200
        assert client.get(f"/v1/workspaces/{wid_a}", headers=_auth("b")).status_code == 403
        assert client.get(f"/v1/workspaces/{wid_a}").status_code == 401


# ---------------------------------------------------------------------------
# Exactly one active personal workspace per user
# ---------------------------------------------------------------------------

class TestOnePersonalWorkspace:
    def test_provision_sets_owner(self, db):
        user = get_or_create_user(db, _claims("p@x.com"))
        ws = provision_workspace(db, user)
        db.commit()
        assert ws.owner_user_id == user.id
        assert ws.password_hash          # agents still get a machine credential

    def test_get_or_create_is_idempotent(self, db):
        user = get_or_create_user(db, _claims("p2@x.com"))
        first = get_or_create_owned_workspace(db, user)
        db.commit()
        second = get_or_create_owned_workspace(db, user)
        db.commit()
        assert first.id == second.id

    def test_two_active_workspaces_rejected_by_the_database(self, db):
        """The guarantee is the partial unique index, not application code."""
        user = get_or_create_user(db, _claims("p3@x.com"))
        provision_workspace(db, user)
        db.commit()

        db.add(Workspace(name="Second", slug="dup1", password_hash="t2",
                         owner_user_id=user.id, status="active"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_a_deleted_workspace_frees_the_slot(self, db):
        """The index only covers active rows, so re-provisioning stays possible."""
        user = get_or_create_user(db, _claims("p4@x.com"))
        first = provision_workspace(db, user)
        db.commit()
        first.status = "deleted"
        db.commit()

        second = Workspace(name="Fresh", slug="dup2", password_hash="t3",
                           owner_user_id=user.id, status="active")
        db.add(second)
        db.commit()          # must not raise
        assert second.owner_user_id == user.id

    def test_account_endpoint_returns_the_same_workspace_twice(self, client, db, monkeypatch):
        _stub_identity(monkeypatch, {"a": _claims("a@x.com")})
        _seed_user(db, "a@x.com", "usera")
        first = client.get("/v1/account/workspace", headers=_auth("a"))
        second = client.get("/v1/account/workspace", headers=_auth("a"))
        assert first.status_code == second.status_code == 200
        assert first.json()["data"]["workspaceId"] == second.json()["data"]["workspaceId"]

    def test_account_endpoint_requires_identity(self, client, monkeypatch):
        _stub_identity(monkeypatch, {})
        assert client.get("/v1/account/workspace").status_code == 401
        assert client.get("/v1/account/workspace", headers=_auth("forged")).status_code == 401


# ---------------------------------------------------------------------------
# Agents keep working — they authenticate as machines, not humans
# ---------------------------------------------------------------------------

class TestAgentMachineTokenAccess:
    def _process(self, db, ws, token=None, bearer=None):
        event = SimpleNamespace(network=None)
        ctx = SimpleNamespace(
            extra={"workspace": ws, "token": token, "bearer_token": bearer},
            db=db,
        )
        return asyncio.run(AuthMod().process(event, ctx))

    def test_agent_token_passes_the_event_write_path(self, db):
        _, ws = _owned_workspace(db, "a@x.com", "agt1")
        assert self._process(db, ws, token="tok") is not None

    def test_wrong_agent_token_rejected(self, db):
        _, ws = _owned_workspace(db, "a@x.com", "agt2")
        assert self._process(db, ws, token="wrong") is None

    def test_anonymous_event_write_rejected(self, db):
        _, ws = _owned_workspace(db, "a@x.com", "agt3")
        assert self._process(db, ws) is None

    def test_owner_bearer_passes_the_event_write_path(self, db, monkeypatch):
        _stub_identity(monkeypatch, {"a": _claims("a@x.com")})
        _, ws = _owned_workspace(db, "a@x.com", "agt4")
        assert self._process(db, ws, bearer="a") is not None

    def test_pai_reaches_the_workspace_api_with_the_machine_token(self, client, db, monkeypatch):
        """PAI Counselor/Operator call the real HTTP API with password_hash."""
        _stub_identity(monkeypatch, {"a": _claims("a@x.com")})
        _seed_user(db, "a@x.com", "usera")
        data = client.get("/v1/account/workspace", headers=_auth("a")).json()["data"]
        r = client.get(f"/v1/workspaces/{data['workspaceId']}",
                       headers={"X-Workspace-Token": data["token"]})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /v1/workspaces/{id}/me
# ---------------------------------------------------------------------------

class TestMeEndpoint:
    def test_owner_identity(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"},
                          headers=_auth("al")).json()["data"]["workspaceId"]
        me = client.get(f"/v1/workspaces/{wid}/me", headers=_auth("al")).json()["data"]
        assert me["email"] == "al@x.com"
        assert me["authenticated"] is True
        assert me["isOwner"] is True
        assert me["tokenAccess"] is False

    def test_token_access_is_not_an_identity(self, client):
        data = client.post("/v1/workspaces", json={"name": "WS"}).json()["data"]
        me = client.get(
            f"/v1/workspaces/{data['workspaceId']}/me",
            headers={"X-Workspace-Token": data["token"]},
        ).json()["data"]
        assert me["authenticated"] is False
        assert me["isOwner"] is False
        assert me["tokenAccess"] is True
        assert me["email"] is None

    def test_anonymous_denied(self, client, monkeypatch):
        _stub_identity(monkeypatch, {"al": _claims("al@x.com")})
        wid = client.post("/v1/workspaces", json={"name": "WS"},
                          headers=_auth("al")).json()["data"]["workspaceId"]
        assert client.get(f"/v1/workspaces/{wid}/me").status_code == 401


# ---------------------------------------------------------------------------
# Identity comes from the verified token, never from a payload
# ---------------------------------------------------------------------------

class TestIdentityIsNeverTakenFromPayloads:
    def test_get_or_create_user_idempotent(self, db):
        a = get_or_create_user(db, _claims("dup@x.com"))
        db.commit()
        b = get_or_create_user(db, _claims("dup@x.com"))
        db.commit()
        assert a.id == b.id

    def test_claimed_email_in_a_payload_grants_nothing(self, db, monkeypatch):
        """A payload claiming someone's email must not become access.

        The old path really did this: posting with `sender_email` upserted a
        collaborator row, and collaborator rows were an access grant. Asserted
        on the access decision itself rather than an HTTP status, so the test
        cannot pass merely because the request failed validation first.
        """
        _stub_identity(monkeypatch, {"b": _claims("b@x.com")})
        _, ws_a = _owned_workspace(db, "a@x.com", "forge1", token="tok-a")
        db.add(User(email="b@x.com"))
        db.commit()

        payload = {"content": "hi", "sender_email": "a@x.com",
                   "sender_display_name": "A", "role": "owner", "owner": True}

        from app.mods.workspace_mod import _handle_message_posted  # noqa: F401
        # Nothing may read identity out of that payload: B is still not the
        # owner of A's workspace, before or after anyone posts it.
        assert is_workspace_owner(db, ws_a, "Bearer b") is False
        assert verify_workspace_access(ws_a, None, "Bearer b", db=db) is False
        # And the claimed email does not become a credential either.
        assert verify_workspace_access(ws_a, payload["sender_email"], None, db=db) is False

    def test_no_payload_derived_collaborator_writer_exists(self):
        """`_upsert_human_collaborator` was the function that turned a payload
        email into a workspace grant. It must be gone, not merely unused."""
        import app.mods.workspace_mod as workspace_mod
        assert not hasattr(workspace_mod, "_upsert_human_collaborator")

    def test_no_human_membership_tables_remain(self):
        import app.models as models
        assert not hasattr(models, "WorkspaceMembership")
        assert not hasattr(models, "WorkspaceCollaborator")
        # Agents are a different thing entirely and must survive.
        assert hasattr(models, "WorkspaceMember")
