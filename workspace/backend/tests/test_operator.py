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

    def test_delegate_captures_the_calling_thread_as_channel_target(self, client, db, monkeypatch):
        """`ctx.conversation` is the same thread id PAI Counselor's own tool
        context carries — delegate() must save it so the finished run knows
        where to auto-post its result (see cloud_agent._post_response)."""
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_ENABLED", True)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
        monkeypatch.setattr(operator, "_execute", lambda *a, **k: asyncio.sleep(0))

        ctx = ToolContext(workspace_id=ws_id, agent_name="pai", api=FakeApi(), conversation="thread-42")
        result = asyncio.run(operator.delegate(ctx, "Prepare University X application", None, None))

        run = db.execute(select(ExecutionRun).where(ExecutionRun.id == result["data"]["run_id"])).scalar_one()
        assert run.channel_target == "channel/thread-42"

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
        # Plan progress is semantic ("N/M steps done"), never a stand-in for
        # which tools got called — see ExecutionRun in app/models.py. Neither
        # step was confirmed done by VERIFY (no completed_step_ids, status
        # isn't "completed"), so the first stays the one "working" on.
        assert run.plan == [
            {"id": "read_requirements", "title": "Read requirements", "status": "working"},
            {"id": "check_documents", "title": "Check documents", "status": "pending"},
        ]
        assert run.completed_steps == []
        # Tool-call history is tracked separately from plan progress.
        assert run.tool_calls == [{"tool": "tasks.list", "ok": True}]
        assert run.missing == ["recommendation_letter"]
        assert run.approval_required_for == "final_submission"
        assert run.completed_at is not None
        assert run.result["summary"] == "Draft 82% complete."
        assert run.result["tool_calls"] == [{"tool": "tasks.list", "ok": True}]
        assert run.verification["approval_required_for"] == "final_submission"
        assert run.result_type == "text"

    def test_terminal_result_is_auto_posted_to_the_originating_thread(self, client, db, monkeypatch):
        """The whole point of channel_target: when a run finishes, the result
        must reach the student in the same thread automatically — the same
        event-pipeline path a normal cloud-agent reply uses — instead of
        sitting silently in the ExecutionRun row until asked about."""
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
        monkeypatch.setattr(config, "PAI_MODEL", "gpt-5.4-mini")
        monkeypatch.setattr(config, "PAI_BASE_URL", "https://api.openai.com/v1")

        async def fake_chat_completion(**kwargs):
            content = kwargs["messages"][0]["content"]
            if "PLAN phase" in content:
                return '["Look up the deadline"]'
            if "VERIFY phase" in content:
                return '{"status": "completed", "missing": [], "approval_required_for": null, "summary": "The deadline is March 1."}'
            return "Understood."

        async def fake_chat_completion_tools(**kwargs):
            return {"role": "assistant", "content": "Found it."}

        monkeypatch.setattr(operator, "chat_completion", fake_chat_completion)
        monkeypatch.setattr(operator, "chat_completion_tools", fake_chat_completion_tools)

        posted = []

        async def fake_post_response(db_, workspace_id_, channel_target_, agent_name_, content_, depth, **kwargs):
            posted.append((workspace_id_, channel_target_, agent_name_, content_, kwargs))

        monkeypatch.setattr("app.services.cloud_agent._post_response", fake_post_response)

        run = ExecutionRun(
            workspace_id=ws_id, requested_by="openagents:pai",
            objective="When is the application deadline?", status="pending",
            channel_target="channel/thread-42",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id, objective, channel_target = run.id, run.objective, run.channel_target

        asyncio.run(operator._execute(run_id, ws_id, FakeApi(), objective, {}, [], channel_target))

        assert len(posted) == 1
        workspace_id_, channel_target_, agent_name_, content_, kwargs = posted[0]
        assert workspace_id_ == ws_id
        assert channel_target_ == "channel/thread-42"
        assert agent_name_ == pai.PAI_AGENT_NAME
        assert content_ == "The deadline is March 1."
        # Attribution: the frontend tells this apart from an ordinary
        # Counselor reply via message_type + the run id/status in metadata —
        # see cloud_agent._post_response and components/chat/chat-message.tsx.
        assert kwargs["message_type"] == "operator_result"
        assert kwargs["metadata"]["execution_run_id"] == run_id
        assert kwargs["metadata"]["execution_status"] == "completed"

    def test_no_post_attempted_without_a_channel_target(self, client, db, monkeypatch):
        """A run with nowhere to report to (e.g. delegated outside a live
        thread) must not attempt to post anywhere."""
        ws_id = _create_workspace(client)["workspaceId"]
        monkeypatch.setattr(operator, "SessionLocal", lambda: db)
        monkeypatch.setattr(config, "PAI_API_KEY", "test-server-key")
        monkeypatch.setattr(config, "PAI_MODEL", "gpt-5.4-mini")
        monkeypatch.setattr(config, "PAI_BASE_URL", "https://api.openai.com/v1")

        async def fake_chat_completion(**kwargs):
            content = kwargs["messages"][0]["content"]
            if "PLAN phase" in content:
                return '["Look up the deadline"]'
            if "VERIFY phase" in content:
                return '{"status": "completed", "missing": [], "approval_required_for": null, "summary": "Done."}'
            return "Understood."

        async def fake_chat_completion_tools(**kwargs):
            return {"role": "assistant", "content": "Found it."}

        monkeypatch.setattr(operator, "chat_completion", fake_chat_completion)
        monkeypatch.setattr(operator, "chat_completion_tools", fake_chat_completion_tools)

        posted = []
        monkeypatch.setattr(
            "app.services.cloud_agent._post_response",
            lambda *a, **k: posted.append(a) or asyncio.sleep(0),
        )

        run = ExecutionRun(
            workspace_id=ws_id, requested_by="openagents:pai",
            objective="Do something", status="pending",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        asyncio.run(operator._execute(run.id, ws_id, FakeApi(), run.objective, {}, [], None))

        assert posted == []


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
