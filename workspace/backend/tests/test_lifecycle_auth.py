# -*- coding: utf-8 -*-
"""
/v1/leave and /v1/heartbeat credential checks.

These endpoints historically self-supplied the workspace token to the event
pipeline, so anyone who knew a workspace slug and an agent name could mark
the agent offline. Clients have always sent X-Workspace-Token; the server now
verifies it and rejects callers that don't. ENFORCE_AGENT_LIFECYCLE_AUTH
defaults to on, so an unset or unparseable env var fails closed; setting it
to "false" is the deliberate, temporary escape hatch for a legacy fleet.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from app.config import config


def _join(client, workspace, name="agent-life"):
    resp = client.post("/v1/join", json={
        "agent_name": name,
        "token": workspace["token"],
        "network": workspace["id"],
    })
    assert resp.status_code == 200
    return name


@pytest.fixture
def enforce_lifecycle_auth():
    """Enforcement is the default; this pins it so the test states its premise."""
    old = config.ENFORCE_AGENT_LIFECYCLE_AUTH
    config.ENFORCE_AGENT_LIFECYCLE_AUTH = True
    try:
        yield
    finally:
        config.ENFORCE_AGENT_LIFECYCLE_AUTH = old


@pytest.fixture
def legacy_lifecycle_auth():
    """The opt-out: only an explicit "false" restores warn-and-accept."""
    old = config.ENFORCE_AGENT_LIFECYCLE_AUTH
    config.ENFORCE_AGENT_LIFECYCLE_AUTH = False
    try:
        yield
    finally:
        config.ENFORCE_AGENT_LIFECYCLE_AUTH = old


@pytest.mark.parametrize("env,expected", [
    (None, True),        # unset
    ("", True),          # present but empty
    ("banana", True),    # unparseable
    ("true", True),
    ("false", False),    # the deliberate opt-out
])
def test_enforcement_default(env, expected):
    """Fail closed: only an explicit falsey value turns enforcement off.

    Imported in a subprocess with a controlled environment — reloading
    app.config in-process would hand every module that already holds a
    reference to `config` a stale object (see test_identity_defaults).
    """
    environ = dict(os.environ)
    environ.pop("ENFORCE_AGENT_LIFECYCLE_AUTH", None)
    if env is not None:
        environ["ENFORCE_AGENT_LIFECYCLE_AUTH"] = env

    out = subprocess.run(
        [sys.executable, "-c",
         "from app.config import config; print(config.ENFORCE_AGENT_LIFECYCLE_AUTH)"],
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        env=environ, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(expected)


class TestLeaveAuth:
    def test_leave_with_valid_token(self, client, workspace):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/leave",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "offline"

    def test_leave_without_token_rejected_by_default(self, client, workspace):
        """No credentials, no lifecycle change — knowing the slug is not access."""
        name = _join(client, workspace)
        resp = client.post(
            "/v1/leave",
            json={"agent_name": name, "network": workspace["id"]},
        )
        assert resp.status_code == 401

    def test_leave_without_token_accepted_under_legacy_optout(
        self, client, workspace, caplog, legacy_lifecycle_auth
    ):
        """The escape hatch still works, and still says so loudly."""
        name = _join(client, workspace)
        with caplog.at_level("WARNING", logger="app.routers.network"):
            resp = client.post(
                "/v1/leave",
                json={"agent_name": name, "network": workspace["id"]},
            )
        assert resp.status_code == 200
        assert any("/v1/leave" in r.message for r in caplog.records)

    def test_leave_with_bad_token_rejected_when_enforced(
        self, client, workspace, enforce_lifecycle_auth
    ):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/leave",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": "definitely-wrong-token"},
        )
        assert resp.status_code == 401
        # And the agent was not marked offline by the rejected call.
        resp = client.post(
            "/v1/leave",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
        )
        assert resp.status_code == 200

    def test_leave_with_valid_token_still_works_when_enforced(
        self, client, workspace, enforce_lifecycle_auth
    ):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/leave",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
        )
        assert resp.status_code == 200


class TestHeartbeatAuth:
    def test_heartbeat_with_valid_token(self, client, workspace):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/heartbeat",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": workspace["token"], "X-Session-Id": workspace["session_id"]},
        )
        assert resp.status_code == 200

    def test_heartbeat_without_token_rejected_by_default(self, client, workspace):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/heartbeat",
            json={"agent_name": name, "network": workspace["id"]},
        )
        assert resp.status_code == 401

    def test_heartbeat_without_token_accepted_under_legacy_optout(
        self, client, workspace, caplog, legacy_lifecycle_auth
    ):
        name = _join(client, workspace)
        with caplog.at_level("WARNING", logger="app.routers.network"):
            resp = client.post(
                "/v1/heartbeat",
                json={"agent_name": name, "network": workspace["id"]},
            )
        assert resp.status_code == 200
        assert any("/v1/heartbeat" in r.message for r in caplog.records)

    def test_heartbeat_with_bad_token_rejected_when_enforced(
        self, client, workspace, enforce_lifecycle_auth
    ):
        name = _join(client, workspace)
        resp = client.post(
            "/v1/heartbeat",
            json={"agent_name": name, "network": workspace["id"]},
            headers={"X-Workspace-Token": "definitely-wrong-token"},
        )
        assert resp.status_code == 401
