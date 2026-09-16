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


_UNSET = object()


def _context(agent_name: str, allowed=None, granted=_UNSET) -> ToolContext:
    """Build a ToolContext.

    `granted` left unset derives the grant from the agent name (the real
    runtime behaviour); passing `None` explicitly models a caller that never
    declared one, which is exactly the fail-closed case under test.
    """
    if granted is _UNSET:
        granted = capabilities_for_agent(agent_name)
    return ToolContext(
        workspace_id="ws-1", agent_name=agent_name, api=None,
        allowed_tools=allowed, granted_capabilities=granted,
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


def test_unknown_agents_get_no_capabilities():
    """Fails closed on READS too, not just writes.

    A third-party agent in the workspace must not be able to read the
    student's Vault simply because nobody listed it.
    """
    assert capabilities_for_agent("some-future-agent") == frozenset()
    assert capabilities_for_agent("") == frozenset()


def test_unknown_agents_are_offered_no_memory_tools():
    registry = get_tool_registry()
    allowed = registry.tools_for_capabilities(capabilities_for_agent("random-agent"))
    for name in MEMORY_READ_TOOLS + MEMORY_WRITE_TOOLS:
        assert name not in allowed
    # ...but ordinary, capability-free tools still work for them.
    assert "web.search" in allowed


def test_capability_tools_fail_closed_without_a_grant():
    """Registry and policy must AGREE that no grant means refused.

    Regression: `permits()` returned True for `granted=None` while
    `ToolPolicy.authorize` returned False — the registry advertised tools the
    executor then refused.
    """
    registry = get_tool_registry()
    tool = registry.get("memory.remember")

    assert registry.permits(tool, None) is False
    assert registry.permits(tool, frozenset()) is False

    allowed, reason = ToolPolicy().authorize(
        tool, _context("x", allowed=frozenset({"memory.remember"}), granted=None),
    )
    assert not allowed and "capabilit" in reason.lower()


def test_registry_and_policy_agree_for_every_tool():
    """No tool may be advertised to a caller the policy would refuse."""
    registry = get_tool_registry()
    for grant in (None, frozenset(), OPERATOR_CAPABILITIES, COUNSELOR_CAPABILITIES):
        context = _context("probe", granted=grant)
        for tool in registry.all():
            advertised = registry.permits(tool, grant)
            authorized, _ = ToolPolicy(allow_sensitive=True).authorize(tool, context)
            assert advertised == authorized, (
                f"{tool.name}: advertised={advertised} authorized={authorized} "
                f"grant={grant}"
            )


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
    # Including for a caller that never declared a grant at all — this is what
    # keeps every pre-capability caller working unchanged.
    assert registry.permits(registry.get("legacy.tool"), None) is True


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
