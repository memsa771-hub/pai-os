# -*- coding: utf-8 -*-
"""
Tests for the built-in PAI Counselor system agent.

Covers:
- Provisioning is gated on PAI_ENABLED + PAI_API_KEY (self-hosted without a
  key gets no PAI Counselor, so it doesn't break the existing suite).
- New workspaces auto-provision PAI Counselor, which surfaces with builtin=true in both
  /v1/discover and /v1/workspaces/{id}; real agents stay builtin=false.
- PAI Counselor cannot be installed, replaced, modified, or removed by users.
- The assistant tool loop posts a chat reply (LLM stubbed).
"""

import asyncio
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.config import config
from app.models import Channel, CloudAgentConfig, EventRecord, WorkspaceMember


@pytest.fixture
def pai_enabled(monkeypatch):
    """Enable PAI Counselor with a fake server-held key for the duration of a test."""
    monkeypatch.setattr(config, "PAI_ENABLED", True)
    monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
    monkeypatch.setattr(config, "PAI_MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(config, "PAI_BASE_URL", "https://api.openai.com/v1")
    return True


def _create_workspace(client, name="PAI Counselor WS", agent_name="agent-alpha"):
    payload = {
        "name": name,
        "creator_email": "test@example.com",
    }
    if agent_name:
        payload["agent_name"] = agent_name
    resp = client.post("/v1/workspaces", json=payload)
    assert resp.status_code == 200
    return resp.json()["data"]


def _discover(client, ws_id, token):
    resp = client.get("/v1/discover", params={"network": ws_id},
                      headers={"X-Workspace-Token": token})
    assert resp.status_code == 200
    return resp.json()["data"]["agents"]


class TestPaiConfig:
    def test_config_loads_openai_values_from_environment(self, monkeypatch):
        env = os.environ.copy()
        env.update({
            "PAI_ENABLED": "true",
            "PAI_API_KEY": "env-only-test-key",
            "PAI_MODEL": "gpt-5.4-mini",
            "PAI_BASE_URL": "https://api.openai.com/v1",
        })
        result = subprocess.run(
            [sys.executable, "-c", (
                "import json; from app.config import config; "
                "print(json.dumps([config.PAI_ENABLED, config.PAI_API_KEY, "
                "config.PAI_MODEL, config.PAI_BASE_URL]))"
            )],
            env=env, check=True, capture_output=True, text=True,
        )
        assert json.loads(result.stdout) == [
            True, "env-only-test-key", "gpt-5.4-mini", "https://api.openai.com/v1",
        ]

    def test_missing_key_logs_safe_startup_error(self, monkeypatch, caplog):
        from app.services.pai import validate_config

        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "")
        assert validate_config() is False
        assert "PAI Counselor is enabled but PAI_API_KEY is not configured." in caplog.text


class TestProvisioning:
    def test_not_provisioned_without_key(self, client):
        """Default env (no PAI_API_KEY) → no PAI Counselor, existing behavior intact."""
        data = _create_workspace(client)
        addresses = [a["address"] for a in _discover(client, data["workspaceId"], data["token"])]
        assert "openagents:pai" not in addresses

    def test_provisioned_with_key(self, client, pai_enabled):
        data = _create_workspace(client)
        agents = _discover(client, data["workspaceId"], data["token"])
        by_addr = {a["address"]: a for a in agents}

        assert "openagents:pai" in by_addr, "PAI Counselor should be auto-added"
        pai = by_addr["openagents:pai"]
        assert pai["builtin"] is True
        assert pai["agent_type"] == "cloud:placement_ai"
        assert pai["display_name"] == "PAI Counselor"
        assert pai["status"] == "online"
        # A real agent must NOT be flagged builtin.
        assert by_addr["openagents:agent-alpha"]["builtin"] is False

    def test_builtin_flag_in_workspace_detail(self, client, pai_enabled):
        data = _create_workspace(client)
        resp = client.get(f"/v1/workspaces/{data['workspaceId']}",
                          headers={"X-Workspace-Token": data["token"]})
        assert resp.status_code == 200
        agents = {a["agentName"]: a for a in resp.json()["data"]["agents"]}
        assert agents["pai"]["builtin"] is True
        assert agents["agent-alpha"]["builtin"] is False

    def test_provisioning_is_idempotent(self, client, pai_enabled, db):
        """provision_pai twice must not create duplicate rows."""
        from app.models import Workspace
        from app.services.pai import provision_pai

        data = _create_workspace(client)
        ws = db.execute(select(Workspace).where(Workspace.id == data["workspaceId"])).scalar_one()
        added = provision_pai(db, ws)
        assert added is False  # already there from creation
        members = db.execute(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.agent_name == "pai",
        )).scalars().all()
        assert len(members) == 1


class TestSystemAgentProtection:
    def test_pai_cannot_be_removed(self, client, pai_enabled):
        data = _create_workspace(client)
        ws_id, token = data["workspaceId"], data["token"]

        resp = client.request(
            "DELETE", f"/v1/cloud-agents/pai",
            params={"network": ws_id}, headers={"X-Workspace-Token": token},
        )
        assert resp.status_code == 403
        assert resp.json()["message"] == "PAI Counselor is a system agent and cannot be removed."
        assert "openagents:pai" in [a["address"] for a in _discover(client, ws_id, token)]

        resp = client.delete(
            f"/v1/workspaces/{ws_id}/members/pai",
            headers={"X-Workspace-Token": token},
        )
        assert resp.status_code == 403

        resp = client.post(
            "/v1/remove",
            json={"agent_name": "pai", "network": ws_id},
            headers={"X-Workspace-Token": token},
        )
        assert resp.status_code == 403

    def test_pai_cannot_be_installed_or_replaced(self, client, pai_enabled):
        data = _create_workspace(client)
        resp = client.post("/v1/cloud-agents", json={
            "network": data["workspaceId"],
            "agent_name": "pai",
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "api_key": "user-secret",
        }, headers={"X-Workspace-Token": data["token"]})
        assert resp.status_code == 403

        resp = client.post("/v1/cloud-agents", json={
            "network": data["workspaceId"],
            "agent_name": "pai-copy",
            "provider": "placement_ai",
            "model": "deepseek-v4-pro",
            "api_key": "",
        }, headers={"X-Workspace-Token": data["token"]})
        assert resp.status_code == 403


class TestServerResolvedModel:
    def test_builtin_model_comes_from_config_not_row(self, client, pai_enabled, db, monkeypatch):
        """Existing workspaces' PAI Counselor rows keep old model ids; the runtime model
        must come from config so a server-side switch needs no backfill."""
        from sqlalchemy import select
        from app.services.pai import resolve_model

        data = _create_workspace(client)
        cfg = db.execute(select(CloudAgentConfig).where(
            CloudAgentConfig.workspace_id == data["workspaceId"],
            CloudAgentConfig.agent_name == "pai",
        )).scalar_one()

        cfg.model = "deepseek-v4-pro"  # stale persisted value
        monkeypatch.setattr(config, "PAI_MODEL", "deepseek-4-flash")
        assert resolve_model(cfg) == "deepseek-4-flash"

        # Non-builtin agents keep their per-row model.
        cfg.provider = "deepseek"
        assert resolve_model(cfg) == "deepseek-v4-pro"

    def test_server_key_is_neither_stored_nor_exposed(self, client, pai_enabled, db):
        data = _create_workspace(client)
        cfg = db.execute(select(CloudAgentConfig).where(
            CloudAgentConfig.workspace_id == data["workspaceId"],
            CloudAgentConfig.agent_name == "pai",
        )).scalar_one()
        assert cfg.api_key == "__server_managed__"
        assert cfg.api_key != config.PAI_API_KEY

        response = client.get(
            "/v1/cloud-agents",
            params={"network": data["workspaceId"]},
            headers={"X-Workspace-Token": data["token"]},
        )
        assert response.status_code == 200
        assert config.PAI_API_KEY not in response.text
        assert "placement_ai" not in response.text
        assert "gpt-5.4-mini" not in response.text
        assert "__server_managed__" not in response.text


class TestWelcomeExperience:
    def test_fresh_workspace_welcome_is_from_pai(self, client, pai_enabled, db):
        data = _create_workspace(client, name="Fresh Student Workspace", agent_name=None)
        posts = db.execute(select(EventRecord).where(
            EventRecord.network_id == data["workspaceId"],
            EventRecord.type == "workspace.message.posted",
        )).scalars().all()
        welcome = next(p for p in posts if p.source == "openagents:pai")
        assert "PAI Counselor" in welcome.payload["content"]
        assert "education journey" in welcome.payload["content"]
        assert "Claude Code" not in welcome.payload["content"]
        assert "device" not in welcome.payload["content"].lower()
        assert not any(p.source == "openagents:yumi" for p in posts)

    def test_primary_conversation_is_idempotent(self, client, pai_enabled, db):
        from app.models import Workspace
        from app.services.pai import ensure_primary_conversation

        data = _create_workspace(client)
        ws = db.execute(select(Workspace).where(
            Workspace.id == data["workspaceId"],
        )).scalar_one()

        assert ensure_primary_conversation(db, ws) is False
        assert ensure_primary_conversation(db, ws) is False
        channels = db.execute(select(Channel).where(
            Channel.workspace_id == ws.id,
            Channel.name == "pai-counselor",
            Channel.status != "deleted",
        )).scalars().all()
        assert len(channels) == 1

    def test_discovery_backfills_missing_primary_conversation(
        self, client, pai_enabled, db,
    ):
        data = _create_workspace(client)
        channel = db.execute(select(Channel).where(
            Channel.workspace_id == data["workspaceId"],
            Channel.name == "pai-counselor",
        )).scalar_one()
        channel.status = "deleted"
        db.commit()

        agents = _discover(client, data["workspaceId"], data["token"])
        assert any(a["address"] == "openagents:pai" for a in agents)
        db.expire_all()
        channels = db.execute(select(Channel).where(
            Channel.workspace_id == data["workspaceId"],
            Channel.name == "pai-counselor",
        )).scalars().all()
        assert len(channels) == 1
        assert channels[0].status == "active"

    def test_primary_conversation_cannot_be_deleted(self, client, pai_enabled):
        data = _create_workspace(client)
        response = client.patch(
            f"/v1/workspaces/{data['workspaceId']}/channels/pai-counselor",
            json={"status": "deleted"},
            headers={"X-Workspace-Token": data["token"]},
        )
        assert response.status_code == 403


class TestPaiTools:
    """PAI Counselor tools must go through the real HTTP API (in-process ASGI), never
    direct DB queries — pairing codes, nodes, remote commands, threads."""

    def _api(self, data):
        from app.services.pai import WorkspaceApi
        return WorkspaceApi(data["workspaceId"], data["token"])

    def test_create_thread_and_reads_via_api(self, client, pai_enabled):
        from app.services.pai import execute_tool

        data = _create_workspace(client)
        api = self._api(data)

        created = asyncio.run(execute_tool(api, "pai", "create_thread",
                                           {"title": "Planning"}))
        assert created["ok"] and created["channel_name"]

        threads = asyncio.run(execute_tool(api, "pai", "list_threads", {}))
        assert threads["ok"]
        assert any(t["title"] == "Planning" for t in threads["threads"])

        agents = asyncio.run(execute_tool(api, "pai", "list_agents", {}))
        assert agents["ok"]
        pai_row = next(a for a in agents["agents"] if a["name"] == "pai")
        assert pai_row["builtin"] is True

    def test_counselor_cannot_create_tasks_directly(self, client, pai_enabled):
        """tasks.create is real execution (a write), so it's Operator-only —
        Counselor must delegate ("add this to my tasks" -> operator.delegate)
        instead of calling it itself. See PAI_ALLOWED_TOOLS in
        app/services/pai.py and the tool audiences in
        app/tools/builtin/__init__.py."""
        from app.services.pai import execute_tool

        data = _create_workspace(client)
        api = self._api(data)

        result = asyncio.run(execute_tool(api, "pai", "create_task", {
            "title": "Write onboarding docs", "priority": "high",
        }))
        assert result["ok"] is False
        assert result["error"]["code"] == "tool_not_allowed"

    def test_list_tasks_via_counselor(self, client, pai_enabled):
        """Reading tasks stays available to Counselor even though creating
        them doesn't — lightweight reads/context inspection are its job."""
        from app.services.pai import execute_tool
        from app.tools import ToolContext, get_tool_executor

        data = _create_workspace(client)
        api = self._api(data)

        # Seeded the way PAI Operator would (tasks.create is operator-only).
        ctx = ToolContext(workspace_id=data["workspaceId"], agent_name="pai-operator", api=api)
        created = asyncio.run(get_tool_executor().execute("tasks.create", {
            "title": "Write onboarding docs", "priority": "high",
        }, ctx))
        assert created["ok"], created

        # Visible on the real Tasks board endpoint...
        resp = client.get("/v1/tasks", params={"network": data["workspaceId"]},
                          headers={"X-Workspace-Token": data["token"]})
        board = resp.json()["data"]["tasks"]
        assert any(t["title"] == "Write onboarding docs" and t["priority"] == "high"
                   for t in board)

        # ...and via PAI Counselor's read tool.
        listed = asyncio.run(execute_tool(api, "pai", "list_tasks", {}))
        assert listed["ok"]
        assert any(t["title"] == "Write onboarding docs" for t in listed["tasks"])

    def test_state_summary_mentions_agents(self, client, pai_enabled):
        from app.services.pai import workspace_state_summary

        data = _create_workspace(client)
        summary = asyncio.run(workspace_state_summary(self._api(data)))
        assert "agent-alpha" in summary


class TestAssistantLoop:
    def test_assistant_posts_chat(self, client, pai_enabled, db, monkeypatch):
        """The assistant tool loop posts a chat reply (LLM stubbed, no tools)."""
        from app.models import Workspace
        from app.services import cloud_agent

        data = _create_workspace(client)
        ws_id = data["workspaceId"]
        channel_name = "pai-counselor"
        channel_target = f"channel/{channel_name}"

        request = {}

        async def fake_chat_completion_tools(**kwargs):
            request.update(kwargs)
            return {"role": "assistant", "content": "Hi! I'm PAI Counselor, welcome aboard."}

        monkeypatch.setattr(cloud_agent, "chat_completion_tools", fake_chat_completion_tools)

        cfg = db.execute(select(CloudAgentConfig).where(
            CloudAgentConfig.workspace_id == ws_id,
            CloudAgentConfig.agent_name == "pai",
        )).scalar_one()

        event_data = {
            "target": channel_target,
            "payload": {"content": "hello", "message_type": "chat"},
            "metadata": {"target_agents": ["pai"]},
        }

        asyncio.run(cloud_agent._invoke_assistant_agent(db, ws_id, event_data, cfg, 0))

        assert request["api_key"] == "test-server-key"
        assert request["model"] == "gpt-5.4-mini"
        assert request["base_url"] == "https://api.openai.com/v1"
        assert request["provider"] == "placement_ai"
        assert request["tools"]

        posts = db.execute(select(EventRecord).where(
            EventRecord.network_id == ws_id,
            EventRecord.source == "openagents:pai",
            EventRecord.type == "workspace.message.posted",
        )).scalars().all()
        assert any((p.payload or {}).get("content", "").startswith("Hi! I'm PAI Counselor") for p in posts)

    def test_openai_error_is_safe_and_does_not_leak_key(
        self, client, pai_enabled, db, monkeypatch, caplog,
    ):
        from app.services import cloud_agent

        data = _create_workspace(client)
        secret = config.PAI_API_KEY
        posted = []

        async def failing_completion(**kwargs):
            raise RuntimeError(f"upstream rejected Authorization: Bearer {secret}")

        async def capture_error(workspace_id, event_data, agent_name, error_text):
            posted.append(error_text)

        monkeypatch.setattr(cloud_agent, "chat_completion_tools", failing_completion)
        monkeypatch.setattr(cloud_agent, "_post_error_message", capture_error)
        monkeypatch.setattr(cloud_agent, "SessionLocal", lambda: db)

        asyncio.run(cloud_agent.invoke_cloud_agents(data["workspaceId"], {
            "target": f"channel/{data['channel']['name']}",
            "payload": {"content": "hello", "message_type": "chat"},
            "metadata": {"target_agents": ["pai"]},
        }))

        assert posted == [
            "PAI Counselor could not reach the language service right now. "
            "Please try again shortly."
        ]
        assert secret not in caplog.text
        assert secret not in "".join(posted)


class TestNamespaceGuard:
    def test_clash_skips_and_leaves_session_clean(self, client, db, monkeypatch):
        """A member displaying as "pai" blocks the backfill — and the bail-out
        must not leave a pending CloudAgentConfig in the shared session, or the
        next workspace's commit would persist it (P1, review round 3)."""
        # Create the workspace with PAI Counselor disabled so nothing is provisioned yet.
        data = _create_workspace(client)
        resp = client.patch(
            f"/v1/workspaces/{data['workspaceId']}/members/agent-alpha",
            json={"display_name": "pai"},
            headers={"X-Workspace-Token": data["token"]},
        )
        assert resp.status_code == 200

        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")

        from app.models import Workspace
        from app.services.pai import provision_pai
        ws = db.execute(
            select(Workspace).where(Workspace.id == data["workspaceId"])
        ).scalar_one()

        assert provision_pai(db, ws) is False
        assert len(db.new) == 0, f"pending orphans: {db.new}"

        # A later commit (e.g. for the next workspace in the backfill loop)
        # must not persist anything for this workspace.
        db.commit()
        cfgs = db.execute(
            select(CloudAgentConfig).where(
                CloudAgentConfig.workspace_id == data["workspaceId"],
            )
        ).scalars().all()
        assert cfgs == []

    def test_backfill_does_not_take_over_real_pai_agent(self, client, db, monkeypatch):
        """A user's daemon agent that happens to be named "pai" keeps its
        type/description — backfill must skip, not take over (review round 4)."""
        data = _create_workspace(client)
        resp = client.post("/v1/join", json={
            "agent_name": "pai",
            "agent_type": "claude",
            "token": data["token"],
            "network": data["workspaceId"],
        })
        assert resp.status_code == 200

        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")

        from app.models import Workspace
        from app.services.pai import provision_pai
        ws = db.execute(
            select(Workspace).where(Workspace.id == data["workspaceId"])
        ).scalar_one()

        assert provision_pai(db, ws) is False
        assert len(db.new) == 0
        db.expire_all()
        member = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data["workspaceId"],
                WorkspaceMember.agent_name == "pai",
            )
        ).scalar_one()
        assert member.agent_type == "claude"

    def test_backfill_does_not_resurrect_removed_real_agent(self, client, db, monkeypatch):
        """A soft-removed real agent named "pai" must stay removed/claude —
        backfill must not rewrite it to online/cloud:placement_ai (round 5)."""
        data = _create_workspace(client)
        client.post("/v1/join", json={
            "agent_name": "pai",
            "agent_type": "claude",
            "token": data["token"],
            "network": data["workspaceId"],
        })
        member = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data["workspaceId"],
                WorkspaceMember.agent_name == "pai",
            )
        ).scalar_one()
        member.status = "removed"
        db.commit()

        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")

        from app.models import Workspace
        from app.services.pai import provision_pai
        ws = db.execute(
            select(Workspace).where(Workspace.id == data["workspaceId"])
        ).scalar_one()

        assert provision_pai(db, ws) is False
        assert len(db.new) == 0
        db.expire_all()
        member = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data["workspaceId"],
                WorkspaceMember.agent_name == "pai",
            )
        ).scalar_one()
        assert member.agent_type == "claude"
        assert member.status == "removed"
