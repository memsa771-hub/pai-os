# -*- coding: utf-8 -*-
"""
Tests for PAI Operator (app/services/operator.py).

Covers:
- operator.delegate / operator.status are registered tools Counselor can call,
  with the risk classes the task requires (delegate=WRITE, status=READ).
- Operator has no WorkspaceMember/CloudAgentConfig row and never appears in
  /v1/discover — the structural guarantee the module docstring claims.
- delegate() creates an ExecutionRun and returns immediately without running
  the loop inline.
- The UNDERSTAND -> PLAN -> EXECUTE -> OBSERVE -> VERIFY loop drives an
  ExecutionRun from pending to a terminal status, using the shared
  ToolRegistry/ToolExecutor (LLM calls stubbed).
- SENSITIVE tools stay blocked by the shared ToolPolicy even though
  Operator's allowed_tools is computed dynamically (not a hardcoded list).
"""

import asyncio

import pytest
from sqlalchemy import select

from app.config import config
from app.models import CloudAgentConfig, ExecutionRun, WorkspaceMember
from app.services import operator, pai
from app.tools import ToolContext, ToolDefinition, ToolExecutor, ToolRegistry, ToolRisk, get_tool_registry


@pytest.fixture
def pai_enabled(monkeypatch):
    monkeypatch.setattr(config, "PAI_ENABLED", True)
    monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
    monkeypatch.setattr(config, "PAI_MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(config, "PAI_BASE_URL", "https://api.openai.com/v1")
    return True


def _create_workspace(client, name="Operator WS", agent_name="agent-alpha"):
    resp = client.post("/v1/workspaces", json={
        "name": name, "creator_email": "operator-test@example.com", "agent_name": agent_name,
    })
    assert resp.status_code == 200
    return resp.json()["data"]


class TestToolRegistration:
    def test_operator_tools_are_registered_with_expected_risk(self):
        registry = get_tool_registry()
        delegate_tool = registry.get("operator.delegate")
        status_tool = registry.get("operator.status")
        assert delegate_tool is not None and delegate_tool.risk is ToolRisk.WRITE
        assert status_tool is not None and status_tool.risk is ToolRisk.READ

    def test_pai_counselor_can_call_both(self):
        assert "operator.delegate" in pai.PAI_ALLOWED_TOOLS
        assert "operator.status" in pai.PAI_ALLOWED_TOOLS


class TestNoAgentIdentity:
    def test_operator_has_no_member_or_cloud_agent_row(self, client, pai_enabled, db):
        """Provisioning PAI Counselor must never create anything for Operator —
        the whole point is that there is nothing an agent listing could find."""
        data = _create_workspace(client)
        ws_id = data["workspaceId"]

        member = db.execute(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.agent_name == operator.PAI_OPERATOR_AGENT_NAME,
        )).scalar_one_or_none()
        cfg = db.execute(select(CloudAgentConfig).where(
            CloudAgentConfig.workspace_id == ws_id,
            CloudAgentConfig.agent_name == operator.PAI_OPERATOR_AGENT_NAME,
        )).scalar_one_or_none()
        assert member is None
        assert cfg is None

    def test_operator_never_appears_in_discover(self, client, pai_enabled):
        data = _create_workspace(client)
        resp = client.get("/v1/discover", params={"network": data["workspaceId"]},
                           headers={"X-Workspace-Token": data["token"]})
        assert resp.status_code == 200
        names = {a.get("address", "") for a in resp.json()["data"]["agents"]}
        assert not any(operator.PAI_OPERATOR_AGENT_NAME in n for n in names)


class FakeApi:
    """Enough of WorkspaceApi for the execute loop's workspace_state_summary
    call and for tool handlers that only ever call .get()/.post()."""

    def __init__(self):
        self.calls = []

    async def get(self, path, **params):
        self.calls.append(("GET", path, params))
        return {"ok": True, "data": {"agents": [], "channels": []}}

    async def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return {"ok": True, "data": {}}


class TestDelegate:
    def test_delegate_creates_a_run_and_returns_immediately(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")

        started = []

        async def fake_execute(*args, **kwargs):
            started.append(args)

        monkeypatch.setattr(operator, "_execute", fake_execute)

        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi())
        result = asyncio.run(operator.delegate(ctx, "Prepare University X application", None, ["application_123"]))

        assert result["ok"] is True
        assert result["data"]["status"] == "pending"
        run = db.execute(select(ExecutionRun).where(ExecutionRun.id == result["data"]["run_id"])).scalar_one()
        assert run.workspace_id == ws_id
        assert run.requested_by == "openagents:pai"
        assert run.objective == "Prepare University X application"
        assert run.context_refs == ["application_123"]

    def test_delegate_rejects_empty_objective(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi())
        result = asyncio.run(operator.delegate(ctx, "   ", None, None))
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_arguments"

    def test_delegate_unavailable_without_server_config(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_ENABLED", False)
        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi())
        result = asyncio.run(operator.delegate(ctx, "do something", None, None))
        assert result["ok"] is False
        assert result["error"]["code"] == "operator_unavailable"


class TestStatus:
    def test_status_with_no_runs(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi())
        result = asyncio.run(operator.get_status(ctx, None))
        assert result == {"ok": True, "data": {"status": "none"}}

    def test_status_reads_the_latest_run(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        run = ExecutionRun(workspace_id=ws_id, requested_by="openagents:pai", objective="X", status="executing")
        db.add(run)
        db.commit()
        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi())
        result = asyncio.run(operator.get_status(ctx, None))
        assert result["ok"] is True
        assert result["data"]["objective"] == "X"
        assert result["data"]["status"] == "executing"


class TestExecutionLoop:
    def test_full_loop_reaches_a_terminal_status(self, client, db, monkeypatch):
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
        monkeypatch.setattr(config, "PAI_MODEL", "gpt-5.4-mini")
        monkeypatch.setattr(config, "PAI_BASE_URL", "https://api.openai.com/v1")

        text_calls = []

        async def fake_chat_completion(**kwargs):
            content = kwargs["messages"][0]["content"]
            text_calls.append(content)
            if "PLAN phase" in content:
                return '["Read requirements", "Check documents"]'
            if "VERIFY phase" in content:
                return (
                    '{"status": "needs_user_action", "missing": ["recommendation_letter"], '
                    '"approval_required_for": "final_submission", "summary": "Draft 82% complete."}'
                )
            return "Understood: the student wants an application prepared."

        tool_calls_made = []

        async def fake_chat_completion_tools(**kwargs):
            if not tool_calls_made:
                tool_calls_made.append(1)
                return {
                    "role": "assistant", "content": "",
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "tasks__list", "arguments": "{}"},
                    }],
                }
            return {"role": "assistant", "content": "Checked requirements and existing tasks."}

        monkeypatch.setattr(operator, "chat_completion", fake_chat_completion)
        monkeypatch.setattr(operator, "chat_completion_tools", fake_chat_completion_tools)

        run = ExecutionRun(
            workspace_id=ws_id, requested_by="openagents:pai",
            objective="Prepare University X application", status="pending",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        asyncio.run(operator._execute(run.id, ws_id, FakeApi(), run.objective, {}, []))

        # _execute closes its own session when it finishes, so the run
        # instance above is stale — read it back fresh instead of refreshing.
        run = db.execute(select(ExecutionRun).where(ExecutionRun.id == run.id)).scalar_one()
        assert run.status == "needs_user_action"
        assert run.plan == ["Read requirements", "Check documents"]
        assert run.completed_steps == ["tasks.list"]
        assert run.missing == ["recommendation_letter"]
        assert run.approval_required_for == "final_submission"
        assert run.completed_at is not None


class TestPolicyIsRespected:
    def test_sensitive_tool_is_blocked_even_when_dynamically_discovered(self):
        """Operator's allowed_tools is computed from the live registry (never
        a hardcoded list) — but the shared ToolPolicy still denies SENSITIVE
        tools regardless of what's in that allow-list."""
        async def handler(_ctx, _args):
            return {"done": True}

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "application.submit", "Submit", {"type": "object", "properties": {}},
            "application", ToolRisk.SENSITIVE, handler,
        ))
        executor = ToolExecutor(registry)
        ctx = ToolContext(
            workspace_id="ws-1", agent_name=operator.PAI_OPERATOR_AGENT_NAME, api=FakeApi(),
            allowed_tools=frozenset({"application.submit"}),
        )
        result = asyncio.run(executor.execute("application.submit", {}, ctx))
        assert result["error"]["code"] == "tool_not_allowed"
