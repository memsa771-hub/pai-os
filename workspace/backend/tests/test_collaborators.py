# -*- coding: utf-8 -*-
"""
Tests for the legacy email-based collaborator ACL.

Placement AI v2.0 removed the product-facing "add/list/remove collaborators"
endpoints (a student's workspace is private, with no invite path) — see
app/routers/workspaces.py. What's left here:

  - The `WorkspaceCollaborator` row and its bearer-based access fallback still
    work (access.py's `resolve_user_role` legacy path), for self-hosted/
    machine deployments that still rely on it.
  - `POST /{workspace_id}/presence` — the still-active "register my own
    presence" endpoint used by the mention picker / push targeting — kept.
"""

from unittest.mock import patch


def _mock_firebase(email):
    """Make an identity bearer resolve to the given email."""
    claims = (
        {"provider": "supabase", "email": email, "supabase_uid": None, "display_name": None}
        if email
        else None
    )
    return patch("app.firebase_auth.verify_supabase_claims", return_value=claims)


class TestCollaboratorLegacyAccess:
    """The WorkspaceCollaborator row is no longer product-addable, but a row
    that already exists (self-hosted/legacy data) must still grant access —
    this is access.py's fallback path in `resolve_user_role`."""

    def test_collaborator_bearer_grants_access(self, client, db, workspace):
        from app.models import WorkspaceCollaborator
        db.add(WorkspaceCollaborator(workspace_id=workspace["id"], email="collab@example.com", role="editor"))
        db.commit()

        with _mock_firebase("collab@example.com"):
            resp = client.get(
                f"/v1/workspaces/{workspace['id']}",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["code"] == 0

    def test_non_collaborator_bearer_rejected(self, client, workspace):
        with _mock_firebase("stranger@example.com"):
            resp = client.get(
                f"/v1/workspaces/{workspace['id']}",
                headers={"Authorization": "Bearer fake-token"},
            )
            # A valid-but-unrecognized identity is 403 (not a member); only a
            # missing/invalid bearer is 401 — see _workspace_access_denied.
            assert resp.json()["code"] == 403

    def test_collaborator_can_send_event(self, client, db, workspace):
        """A legacy collaborator can still send events via the pipeline."""
        from app.models import WorkspaceCollaborator
        db.add(WorkspaceCollaborator(workspace_id=workspace["id"], email="collab@example.com", role="editor"))
        db.commit()

        with _mock_firebase("collab@example.com"):
            resp = client.post(
                "/v1/events",
                json={
                    "type": "workspace.message.posted",
                    "source": "human:collab",
                    "target": f"channel/{workspace['channel']}",
                    "network": workspace["id"],
                    "payload": {"content": "Hello from collaborator", "sender_type": "human"},
                },
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["code"] == 0


class TestPresencePing:
    AVATAR = "data:image/png;base64,iVBORw0KGgo="

    def _account(self, db, email, display_name=None, avatar_url=None):
        from app.models import User
        user = User(email=email, display_name=display_name, avatar_url=avatar_url)
        db.add(user)
        db.commit()
        return user

    def test_presence_ping_returns_the_avatar(self, client, db, workspace):
        """Self-registration on workspace open is where most rows come from."""
        self._account(db, "carol@example.com", "Carol", self.AVATAR)
        resp = client.post(
            f"/v1/workspaces/{workspace['id']}/presence",
            json={"senderEmail": "Carol@Example.com", "senderDisplayName": "C"},
            headers={"X-Workspace-Token": workspace["token"]},
        )
        data = resp.json()["data"]
        # Matched case-insensitively: the collaborator row is lowercased on
        # write, the account row is not guaranteed to be.
        assert data["avatarUrl"] == self.AVATAR

    def test_presence_ping_ignores_a_spoofed_email_when_bearer_present(self, client, db, workspace):
        """A verified bearer identity always wins over the request body — a
        signed-in student can only ever register their OWN presence."""
        self._account(db, "real@example.com", "Real Person")
        with _mock_firebase("real@example.com"):
            resp = client.post(
                f"/v1/workspaces/{workspace['id']}/presence",
                json={"senderEmail": "someone-else@example.com"},
                headers={
                    "X-Workspace-Token": workspace["token"],
                    "Authorization": "Bearer fake-token",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["email"] == "real@example.com"
