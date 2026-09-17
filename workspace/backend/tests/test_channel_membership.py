# -*- coding: utf-8 -*-
"""
Tests for channel.join / channel.leave authorization + routine-channel lock.
"""


import app.access as access
import app.event_identity as event_identity


def _as_owner(monkeypatch, email="test@example.com"):
    """The workspace owner. A human source now requires a verified bearer —
    writing "human:user" in the body no longer makes you one."""
    claims = {"provider": "supabase", "email": email, "supabase_uid": "uid",
              "apple_sub": None, "display_name": "Student"}
    resolve = lambda tok: claims if tok == "owner-tok" else None
    monkeypatch.setattr(event_identity, "verify_identity_claims", resolve)
    monkeypatch.setattr(access, "verify_identity_claims", resolve)
    return {"Authorization": "Bearer owner-tok"}


def _as_agent(client, workspace, agent_name):
    """Join `agent_name` and return headers carrying ITS session.

    The session is what names an agent to the server, so a test cannot assert
    "agent-beta did X" while holding agent-alpha's credentials."""
    join = client.post("/v1/join", json={
        "agent_name": agent_name,
        "token": workspace["token"],
        "network": workspace["id"],
    }).json()["data"]
    return {"X-Workspace-Token": workspace["token"],
            "X-Session-Id": join["session_id"]}


def _post_event(client, workspace, *, etype, headers, channel, agent_name):
    return client.post(
        "/v1/events",
        json={
            "type": etype,
            "target": f"channel/{channel}",
            "network": workspace["id"],
            "payload": {"channel": channel, "agent_name": agent_name},
        },
        headers=headers,
    )


class TestChannelJoinAuth:
    def test_human_can_invite(self, client, workspace, monkeypatch):
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.join",
            headers=_as_owner(monkeypatch),
            channel=channel,
            agent_name="agent-alpha",
        )
        assert resp.status_code == 200, resp.text

    def test_unrelated_agent_cannot_invite(self, client, workspace):
        """Random openagents source can't add an agent to a channel they don't own."""
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.join",
            headers=_as_agent(client, workspace, "random-bystander"),
            channel=channel,
            agent_name="agent-alpha",
        )
        assert resp.status_code == 403, resp.text
        assert "forbidden" in resp.json()["message"].lower()

    def test_agent_can_join_self(self, client, workspace):
        """An agent can join a channel as itself (the agent_name in payload)."""
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.join",
            headers=_as_agent(client, workspace, "agent-beta"),
            channel=channel,
            agent_name="agent-beta",
        )
        assert resp.status_code == 200, resp.text

    def test_join_routine_channel_rejected(self, client, workspace, monkeypatch):
        """routines:* channels are locked — even humans can't add agents."""
        resp = _post_event(
            client, workspace,
            etype="network.channel.join",
            headers=_as_owner(monkeypatch),
            channel="routines:agent-alpha",
            agent_name="some-other-agent",
        )
        assert resp.status_code == 403, resp.text
        assert "routine_channel_locked" in resp.json()["message"]


class TestChannelLeaveAuth:
    def test_human_can_remove(self, client, workspace, monkeypatch):
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.leave",
            headers=_as_owner(monkeypatch),
            channel=channel,
            agent_name="agent-alpha",
        )
        assert resp.status_code == 200, resp.text

    def test_unrelated_agent_cannot_remove(self, client, workspace):
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.leave",
            headers=_as_agent(client, workspace, "random-bystander"),
            channel=channel,
            agent_name="agent-alpha",
        )
        assert resp.status_code == 403, resp.text

    def test_agent_can_remove_self(self, client, workspace):
        channel = workspace["channel"]["name"]
        resp = _post_event(
            client, workspace,
            etype="network.channel.leave",
            headers={"X-Workspace-Token": workspace["token"],
                     "X-Session-Id": workspace["session_id"]},
            channel=channel,
            agent_name="agent-alpha",
        )
        assert resp.status_code == 200, resp.text

    def test_leave_routine_channel_rejected(self, client, workspace, monkeypatch):
        resp = _post_event(
            client, workspace,
            etype="network.channel.leave",
            headers=_as_owner(monkeypatch),
            channel="routines:agent-alpha",
            agent_name="agent-alpha",
        )
        assert resp.status_code == 403, resp.text
        assert "routine_channel_locked" in resp.json()["message"]
