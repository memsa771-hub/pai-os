import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.capabilities as capability_module
from app.capabilities import CapabilityContract, CapabilityRegistry, CapabilityRouter, FallbackPolicy
from app.capabilities.context import CapabilityExecutionContext
from app.capabilities.execution_policy import resolve_run_policy
from app.capabilities.manifest import CapabilitySchemaError
from app.capabilities.permissions import OPERATOR_PLATFORM_PERMISSIONS
from app.capabilities.tool_broker import CapabilityToolBroker, CapabilityToolUnavailable
from app.models import ExecutionRun
from app.services import operator, pai
from app.tools import AUDIENCE_OPERATOR, ToolContext, ToolDefinition, ToolExecutor, ToolRegistry, ToolRisk
from app.tools.builtin import capabilities as capability_tools
from scripts.counselor_eval_support import StudentSession


async def _handler(context, payload):
    return {"result": payload}


def contract(*, capability_id="example.owner", task_type="owned_task",
             fallback=FallbackPolicy.FORBIDDEN, handler=_handler, **changes):
    values = dict(
        id=capability_id, version="1.0.0", name="Owner", description="Owns a test task.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        handler=handler, owns_task_types=frozenset({task_type}), fallback_policy=fallback,
    )
    values.update(changes)
    return CapabilityContract(**values)


def test_task_owner_resolution_duplicate_rejection_and_per_run_surface():
    registry = CapabilityRegistry()
    owner = registry.register(contract())
    assert registry.owner_for_task_type("owned_task") is owner
    assert CapabilityRouter(registry).resolve_task("owned_task") is owner
    with pytest.raises(ValueError, match="Duplicate task ownership"):
        registry.register(contract(capability_id="example.other"))

    available = frozenset({"capability.list", "capability.describe", "capability.invoke",
                           "profile.propose", "files.read", "web.search", "browser.open"})
    owned = resolve_run_policy(registry, "owned_task")
    assert owned.allowed_tools(available) == frozenset({
        "capability.list", "capability.describe", "capability.invoke", "profile.propose",
    })
    assert resolve_run_policy(registry, "unowned_task").allowed_tools(available) == available


@pytest.mark.asyncio
async def test_task_type_is_persisted_by_delegation():
    with StudentSession() as student:
        ctx = ToolContext(student.workspace_id, "pai", object(), conversation="pai-counselor")
        with patch.object(operator, "is_available", return_value=True), \
                patch.object(operator, "_execute", AsyncMock()):
            result = await operator.delegate(
                ctx, "Do the owned work", {}, None, "discovery", "owned_task",
            )
            await asyncio.sleep(0)
        with student.factory() as db:
            run = db.get(ExecutionRun, result["data"]["run_id"])
            assert run.task_type == "owned_task"
            assert operator.serialize_run(run)["task_type"] == "owned_task"


@pytest.mark.asyncio
async def test_tool_broker_allows_declared_tool_blocks_undeclared_and_uses_policy():
    calls = []

    async def search(ctx, args):
        calls.append(args)
        return {"items": [args["query"]]}

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "web.search", "Search", {"type": "object", "properties": {"query": {"type": "string"}},
                                  "required": ["query"], "additionalProperties": False},
        "web", ToolRisk.READ, search, audiences=frozenset({AUDIENCE_OPERATOR}),
    ))
    registry.register(ToolDefinition(
        "danger.act", "Sensitive", {"type": "object", "properties": {}, "additionalProperties": False},
        "danger", ToolRisk.SENSITIVE, search, audiences=frozenset({AUDIENCE_OPERATOR}),
    ))
    host = ToolContext("w", "pai-operator", object(), audience=AUDIENCE_OPERATOR,
                       allowed_tools=frozenset({"web.search", "danger.act"}), granted_capabilities=frozenset())
    declared = contract(required_tools=frozenset({"web.search"}), permissions=frozenset({"web.read"}))
    broker = CapabilityToolBroker(
        contract=declared, registry=registry, executor=ToolExecutor(registry),
        host_context=host, platform_permissions=OPERATOR_PLATFORM_PERMISSIONS,
    )
    assert (await broker.invoke("web.search", {"query": "AI"}))["ok"]
    assert calls == [{"query": "AI"}]
    with pytest.raises(CapabilityToolUnavailable, match="undeclared"):
        await broker.invoke("danger.act", {})

    sensitive = contract(required_tools=frozenset({"danger.act"}), permissions=frozenset({"danger.act"}))
    sensitive_broker = CapabilityToolBroker(
        contract=sensitive, registry=registry, executor=ToolExecutor(registry),
        host_context=host, platform_permissions=frozenset({"danger.act"}),
    )
    denied = await sensitive_broker.invoke("danger.act", {})
    assert denied["error"]["code"] == "tool_not_allowed"

    # Even a guessed raw call is denied by the same ToolPolicy when an owned
    # run's host-selected surface excludes it.
    locked_context = ToolContext(
        "w", "pai-operator", object(), audience=AUDIENCE_OPERATOR,
        allowed_tools=frozenset({"capability.invoke"}), granted_capabilities=frozenset(),
    )
    bypass = await ToolExecutor(registry).execute("web.search", {"query": "bypass"}, locked_context)
    assert bypass["error"]["code"] == "tool_not_allowed"
    assert calls == [{"query": "AI"}]


@pytest.mark.asyncio
async def test_discovery_and_recursive_input_output_validation():
    nested_input = {
        "type": "object", "additionalProperties": False, "required": ["student"],
        "properties": {"student": {"type": "object", "required": ["scores"],
            "additionalProperties": False, "properties": {"scores": {"type": "array", "minItems": 1,
                "items": {"type": "integer", "minimum": 0, "maximum": 10}}}}},
    }
    nested_output = {"type": "object", "required": ["result"], "additionalProperties": False,
                     "properties": {"result": {"type": "object", "required": ["ok"],
                         "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}}}

    async def bad_output(context, payload):
        return {"result": {"ok": "yes"}}

    registry = CapabilityRegistry()
    item = registry.register(contract(input_schema=nested_input, output_schema=nested_output,
                                      handler=bad_output, permissions=frozenset()))
    context = CapabilityExecutionContext("w", {}, {}, frozenset(), frozenset())
    with pytest.raises(CapabilitySchemaError):
        await CapabilityRouter(registry).execute(item.id, {"student": {"scores": [11]}}, context)
    with pytest.raises(CapabilitySchemaError):
        await CapabilityRouter(registry).execute(item.id, {"student": {"scores": [8]}}, context)

    with patch.object(capability_module, "_registry", registry):
        listed = await capability_tools.list_capabilities(None, {})
        described = await capability_tools.describe(None, {"capability_id": item.id})
    assert listed["data"]["capabilities"][0]["owned_task_types"] == ["owned_task"]
    assert described["data"]["input_schema"] == nested_input
    assert described["data"]["required_tools"] == []


async def _run_owned(student, fallback, capability_result):
    registry = CapabilityRegistry()
    owner = registry.register(contract(fallback=fallback))
    with student.factory() as db:
        run = ExecutionRun(workspace_id=student.workspace_id, requested_by="openagents:pai",
                           objective="Owned objective", task_type="owned_task", constraints={}, context_refs=[])
        db.add(run)
        db.commit()
        run_id = run.id

    phases = AsyncMock(side_effect=[
        "Understand owned work.",
        '[{"id":"invoke","title":"Invoke owner"}]',
        '{"status":"completed","completed_step_ids":["invoke"],"missing":[],"summary":"Done"}',
    ])
    seen_surfaces = []

    async def model(**kwargs):
        seen_surfaces.append({entry["function"]["name"] for entry in (kwargs.get("tools") or [])})
        if len(seen_surfaces) == 1:
            return {"role": "assistant", "tool_calls": [{"id": "cap", "type": "function", "function": {
                "name": "capability__invoke", "arguments": f'{{"capability_id":"{owner.id}","input":{{}}}}'}}]}
        if len(seen_surfaces) == 2 and fallback is FallbackPolicy.GENERIC_ALLOWED:
            return {"role": "assistant", "tool_calls": [{"id": "web", "type": "function", "function": {
                "name": "web__search", "arguments": '{"query":"fallback"}'}}]}
        return {"role": "assistant", "content": "Finished."}

    executor = SimpleNamespace(execute=AsyncMock(side_effect=[
        capability_result,
        *([{"ok": True, "data": {"items": []}}] if fallback is FallbackPolicy.GENERIC_ALLOWED else []),
    ]))
    with patch.object(capability_module, "_registry", registry), \
            patch.object(operator, "chat_completion", phases), \
            patch.object(operator, "chat_completion_tools", model), \
            patch.object(operator, "_resolve_memory_context", return_value=""), \
            patch.object(pai, "workspace_state_summary", AsyncMock(return_value="workspace")), \
            patch("app.tools.get_tool_executor", return_value=executor), \
            patch.object(operator, "_publish_run_updated"), \
            patch.object(operator, "_post_result", AsyncMock()):
        await operator._execute(run_id, student.workspace_id, None, "Owned objective", {}, [], None)
    with student.factory() as db:
        return db.get(ExecutionRun, run_id), seen_surfaces, executor


@pytest.mark.asyncio
async def test_owned_failure_policies_and_reduced_surface_are_deterministic():
    failure = {"ok": False, "error": {"code": "capability_failed", "message": "provider down"}}
    with StudentSession() as student:
        forbidden, surfaces, executor = await _run_owned(student, FallbackPolicy.FORBIDDEN, failure)
        assert forbidden.status == "failed"
        assert executor.execute.await_count == 1
        assert "web__search" not in surfaces[0] and "capability__invoke" in surfaces[0]

    with StudentSession() as student:
        generic, surfaces, executor = await _run_owned(student, FallbackPolicy.GENERIC_ALLOWED, failure)
        assert generic.status == "completed"
        assert executor.execute.await_count == 2
        assert "web__search" not in surfaces[0]
        assert "web__search" in surfaces[1]

    with StudentSession() as student:
        approval, surfaces, executor = await _run_owned(student, FallbackPolicy.APPROVAL_REQUIRED, failure)
        assert approval.status == "needs_user_action"
        assert approval.completed_at is None
        assert approval.pending_action["purpose"] == "capability_fallback"
        assert executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_capability_approval_signal_immediately_pauses_same_run():
    signal = {"ok": False, "error": {"code": "approval_required", "message": "approve",
              "pending_action": {"kind": "approval", "title": "Approve", "prompt": "Approve?",
                                 "reason": "Sensitive", "options": ["approve", "decline"],
                                 "required": True, "purpose": "capability_invoke",
                                 "capability_id": "example.owner"}}}
    with StudentSession() as student:
        paused, _, executor = await _run_owned(student, FallbackPolicy.GENERIC_ALLOWED, signal)
        assert paused.status == "needs_user_action"
        assert paused.completed_at is None
        assert paused.pending_action["purpose"] == "capability_invoke"
        assert len(paused.tool_calls) == 1
        assert executor.execute.await_count == 1
