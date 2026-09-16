import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .policy import ToolRisk

ToolHandler = Callable[[Any, dict], Awaitable[Any]]
_OPENAI_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments: dict
    category: str
    risk: ToolRisk
    handler: ToolHandler
    openai_name: Optional[str] = None
    # Capabilities this tool requires of its caller. Empty (the default) keeps
    # every existing tool unrestricted; a tool that declares capabilities is
    # only offered to callers whose grant covers them. Declaring this on the
    # tool — rather than excluding names in each caller — is what stops a
    # future memory-write tool leaking into PAI Operator.
    capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def transport_name(self) -> str:
        return self.openai_name or self.name.replace(".", "__")


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._transport_names: dict[str, str] = {}

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        if not _OPENAI_NAME.fullmatch(tool.transport_name):
            raise ValueError(f"Invalid OpenAI tool name: {tool.transport_name}")
        if tool.transport_name in self._transport_names:
            raise ValueError(f"Duplicate tool transport name: {tool.transport_name}")
        if tool.arguments.get("type") != "object":
            raise ValueError(f"Tool arguments must be an object schema: {tool.name}")
        self._tools[tool.name] = tool
        self._transport_names[tool.transport_name] = tool.name
        return tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        canonical = self._transport_names.get(name, name)
        return self._tools.get(canonical)

    def all(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def openai_tools_for_agent(self, allowed_tools=None, granted_capabilities=None) -> list[dict]:
        """Tool schemas to advertise to a model.

        Filtered by the same two rules the executor enforces, so a caller is
        never shown a tool it would be refused — the model cannot be tempted
        by a tool it may not call.
        """
        allowed = None if allowed_tools is None else set(allowed_tools)
        return [
            {"type": "function", "function": {
                "name": tool.transport_name,
                "description": tool.description,
                "parameters": tool.arguments,
            }}
            for tool in self._tools.values()
            if (allowed is None or tool.name in allowed or tool.category in allowed)
            and self.permits(tool, granted_capabilities)
        ]

    @staticmethod
    def permits(tool: ToolDefinition, granted_capabilities=None) -> bool:
        """True if a caller holding `granted_capabilities` may use `tool`.

        ``None`` means an unrestricted caller. Kept here (rather than inlined)
        so callers building an allow-set and the policy enforcing it agree by
        construction.
        """
        required = tool.capabilities
        if not required:
            return True
        if granted_capabilities is None:
            return True
        return set(required) <= set(granted_capabilities)

    def tools_for_capabilities(self, granted_capabilities) -> frozenset[str]:
        """Names of every registered tool a caller with this grant may use.

        This is what keeps discovery dynamic: callers ask for "everything I am
        allowed to use" instead of naming tools, so a newly registered tool is
        picked up automatically — and a newly registered *privileged* tool is
        automatically withheld.
        """
        return frozenset(
            tool.name for tool in self._tools.values()
            if self.permits(tool, granted_capabilities)
        )
