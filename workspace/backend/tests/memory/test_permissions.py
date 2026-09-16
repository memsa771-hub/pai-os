# -*- coding: utf-8 -*-
"""Counselor vs Operator capability enforcement.

Requirement: Operator reads memory and changes nothing; Counselor keeps full
access. Critically, this must hold for tools that do not exist yet — so the
suite includes a tool registered at test time to prove the rule is structural
rather than a list of known names.
"""

import pytest

from app.memory.permissions import (
    COUNSELOR_CAPABILITIES,
    OPERATOR_CAPABILITIES,
    capabilities_for_agent,
)
from app.tools import ToolContext, ToolPolicy, get_tool_registry
from app.tools.policy import Capability, ToolRisk
from app.tools.registry import ToolDefinition, ToolRegistry

MEMORY_WRITE_TOOLS = ("memory.remember", "memory.forget")
MEMORY_READ_TOOLS = ("memory.context", "vault.get", "memory.search", "memory.episodes")


async def _noop(context, args):
    return {"ok": True, "data": {}}


def _context(agent_name: str, allowed=None, granted=None) -> ToolContext:
    return ToolContext(
        workspace_id="ws-1", agent_name=agent_name, api=None,
        allowed_tools=allowed,
        granted_capabilities=granted if granted is not None else capabilities_for_agent(agent_name),
    )


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------

def test_operator_holds_only_read_capabilities():
    assert Capability.VAULT_READ.value in OPERATOR_CAPABILITIES
    assert Capability.MEMORY_READ.value in OPERATOR_CAPABILITIES
    assert Capability.MEMORY_MANAGE.value not in OPERATOR_CAPABILITIES
    assert Capability.VAULT_MANAGE.value not in OPERATOR_CAPABILITIES


def test_counselor_holds_read_and_manage():
    for capability in Capability:
        assert capability.value in COUNSELOR_CAPABILITIES


def test_unknown_agents_default_to_read_only():
    """Defaulting closed — a new agent cannot mutate memory by being forgotten."""
    assert capabilities_for_agent("some-future-agent") == OPERATOR_CAPABILITIES


# ---------------------------------------------------------------------------
# Registry-level filtering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", MEMORY_WRITE_TOOLS)
def test_operator_is_not_offered_memory_write_tools(tool_name):
    """Not merely refused at call time — never advertised to the model."""
    registry = get_tool_registry()
    assert tool_name not in registry.tools_for_capabilities(OPERATOR_CAPABILITIES)

    schemas = registry.openai_tools_for_agent(
        allowed_tools=None, granted_capabilities=OPERATOR_CAPABILITIES,
    )
    names = {s["function"]["name"] for s in schemas}
    assert tool_name.replace(".", "__") not in names


@pytest.mark.parametrize("tool_name", MEMORY_READ_TOOLS)
def test_operator_is_offered_memory_read_tools(tool_name):
    registry = get_tool_registry()
    assert tool_name in registry.tools_for_capabilities(OPERATOR_CAPABILITIES)


@pytest.mark.parametrize("tool_name", MEMORY_WRITE_TOOLS + MEMORY_READ_TOOLS)
def test_counselor_retains_every_memory_tool(tool_name):
    registry = get_tool_registry()
    assert tool_name in registry.tools_for_capabilities(COUNSELOR_CAPABILITIES)


def test_operator_still_gets_ordinary_tools():
    """The grant must not accidentally strip Operator's existing toolset."""
    allowed = get_tool_registry().tools_for_capabilities(OPERATOR_CAPABILITIES)
    for name in ("web.search", "files.read", "browser.open", "tasks.create"):
        assert name in allowed


# ---------------------------------------------------------------------------
# Policy-level enforcement (defence in depth)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", MEMORY_WRITE_TOOLS)
def test_policy_refuses_operator_even_if_the_tool_is_named_directly(tool_name):
    """A model that guesses the name still cannot call it.

    Filtering the advertised list is not enough on its own — this is the check
    that holds if a tool name leaks into the conversation some other way.
    """
    registry = get_tool_registry()
    tool = registry.get(tool_name)
    context = _context("pai-operator", allowed=frozenset({tool_name}))

    allowed, reason = ToolPolicy().authorize(tool, context)
    assert not allowed
    assert "capability" in reason.lower()


@pytest.mark.parametrize("tool_name", MEMORY_WRITE_TOOLS)
def test_policy_allows_counselor(tool_name):
    registry = get_tool_registry()
    tool = registry.get(tool_name)
    context = _context("pai", allowed=frozenset({tool_name}))
    allowed, reason = ToolPolicy().authorize(tool, context)
    assert allowed, reason


@pytest.mark.asyncio
async def test_executor_refuses_operator_memory_write(workspace):
    """End to end through the real executor, not just the policy object."""
    from app.tools import get_tool_executor

    context = _context("pai-operator", allowed=frozenset({"memory.remember"}))
    result = await get_tool_executor().execute(
        "memory.remember", {"content": "should not be saved"}, context,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "tool_not_allowed"


# ---------------------------------------------------------------------------
# The property that matters for the future
# ---------------------------------------------------------------------------

def test_a_newly_registered_write_tool_is_withheld_from_operator_automatically():
    """The point of capabilities: no edit to Operator, no exclusion list.

    A future `memory.bulk_import` declaring MEMORY_MANAGE must be unavailable
    to Operator the moment it is registered.
    """
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "memory.bulk_import", "A memory tool invented after Operator was written.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "memory", ToolRisk.WRITE, _noop,
        capabilities=frozenset({Capability.MEMORY_MANAGE.value}),
    ))
    registry.register(ToolDefinition(
        "memory.bulk_read", "A read-only memory tool invented later.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "memory", ToolRisk.READ, _noop,
        capabilities=frozenset({Capability.MEMORY_READ.value}),
    ))

    operator_tools = registry.tools_for_capabilities(OPERATOR_CAPABILITIES)
    assert "memory.bulk_import" not in operator_tools
    assert "memory.bulk_read" in operator_tools
    assert "memory.bulk_import" in registry.tools_for_capabilities(COUNSELOR_CAPABILITIES)


def test_tools_without_capabilities_are_unaffected():
    """Existing tools declare nothing and must keep working for everyone."""
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "legacy.tool", "Pre-capability tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "legacy", ToolRisk.READ, _noop,
    ))
    assert "legacy.tool" in registry.tools_for_capabilities(OPERATOR_CAPABILITIES)
    assert "legacy.tool" in registry.tools_for_capabilities(frozenset())


def test_sensitive_risk_behaviour_is_preserved():
    """The pre-existing ToolPolicy safety rule must still apply."""
    registry = ToolRegistry()
    tool = registry.register(ToolDefinition(
        "danger.tool", "Sensitive.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "danger", ToolRisk.SENSITIVE, _noop,
    ))
    context = _context("pai", allowed=frozenset({"danger.tool"}))

    allowed, reason = ToolPolicy().authorize(tool, context)
    assert not allowed and "approval" in reason
    allowed, _ = ToolPolicy(allow_sensitive=True).authorize(tool, context)
    assert allowed
