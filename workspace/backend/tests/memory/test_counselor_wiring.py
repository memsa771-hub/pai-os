# -*- coding: utf-8 -*-
"""PAI Counselor's runtime tool loop must carry its capability grant.

The real Counselor loop is `app/services/cloud_agent.py`, not the
`pai.build_tools()` facade. A grant wired only into the facade would look
correct in review and still leave the live path without memory tools.
"""

from app.memory.permissions import (
    COUNSELOR_CAPABILITIES,
    NO_CAPABILITIES,
    capabilities_for_agent,
)
from app.services.operator import PAI_OPERATOR_AGENT_NAME
from app.services.pai import PAI_AGENT_NAME, PAI_ALLOWED_TOOLS, build_tools
from app.tools import get_tool_registry

MEMORY_TOOLS = (
    "memory.context", "vault.get", "memory.search",
    "memory.episodes", "memory.remember", "memory.forget",
)


def test_counselor_allowlist_includes_the_memory_tools():
    for name in MEMORY_TOOLS:
        assert name in PAI_ALLOWED_TOOLS


def test_build_tools_advertises_memory_to_counselor():
    """The facade must offer every memory tool, write tools included."""
    advertised = {t["function"]["name"] for t in build_tools()}
    for name in MEMORY_TOOLS:
        assert name.replace(".", "__") in advertised


def test_counselor_grant_resolves_from_its_agent_name():
    """cloud_agent.py keys the grant on agent_name — that lookup must work."""
    assert capabilities_for_agent(PAI_AGENT_NAME) == COUNSELOR_CAPABILITIES


def test_a_user_added_cloud_agent_gets_no_memory_grant():
    """The Counselor loop serves every cloud agent.

    A user-added agent running the same code path must NOT inherit
    Counselor's memory access just by being a cloud agent.
    """
    assert capabilities_for_agent("my-custom-gpt") == NO_CAPABILITIES

    registry = get_tool_registry()
    allowed = registry.tools_for_capabilities(capabilities_for_agent("my-custom-gpt"))
    for name in MEMORY_TOOLS:
        assert name not in allowed


def test_counselor_sees_more_memory_tools_than_operator():
    registry = get_tool_registry()
    counselor = registry.tools_for_capabilities(
        capabilities_for_agent(PAI_AGENT_NAME)
    )
    operator = registry.tools_for_capabilities(
        capabilities_for_agent(PAI_OPERATOR_AGENT_NAME)
    )

    assert {"memory.remember", "memory.forget"} <= counselor
    assert not ({"memory.remember", "memory.forget"} & operator)
    # Both keep the read tools.
    assert {"memory.context", "vault.get"} <= counselor
    assert {"memory.context", "vault.get"} <= operator


def test_schema_discovery_is_capability_filtered():
    """A caller is never shown a tool the executor would refuse it."""
    registry = get_tool_registry()
    operator_schemas = {
        t["function"]["name"]
        for t in registry.openai_tools_for_agent(
            PAI_ALLOWED_TOOLS,
            granted_capabilities=capabilities_for_agent(PAI_OPERATOR_AGENT_NAME),
        )
    }
    assert "memory__remember" not in operator_schemas
    assert "memory__context" in operator_schemas
