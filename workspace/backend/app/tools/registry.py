import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .policy import ToolRisk

ToolHandler = Callable[[Any, dict], Awaitable[Any]]
_OPENAI_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Who may discover/use a tool — separate from ToolPolicy's authorization
# decision (see policy.py). This gates what shows up in an agent's tool list
# in the first place; ToolPolicy still gets the final say at execution time.
# "counselor" = PAI Counselor, "operator" = PAI Operator, "internal" = never
# offered to a model (debug/admin tools, if any get registered later).
AUDIENCE_COUNSELOR = "counselor"
AUDIENCE_OPERATOR = "operator"
AUDIENCE_INTERNAL = "internal"
DEFAULT_AUDIENCES = frozenset({AUDIENCE_OPERATOR})


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments: dict
    category: str
    risk: ToolRisk
    handler: ToolHandler
    openai_name: Optional[str] = None
    # Defaults to "operator only" — a newly registered tool must opt in to
    # being handed to PAI Counselor, not the other way around, so the
    # execution surface never grows silently as PAI adds tools.
    audiences: frozenset[str] = field(default=DEFAULT_AUDIENCES)

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

    def for_audience(self, audience: str) -> tuple[ToolDefinition, ...]:
        """Tools discoverable by a given audience (see AUDIENCE_* above).

        This is a *discovery* filter — it decides what a caller can even see,
        never whether a specific call is authorized (that stays ToolPolicy's
        job; see tools/policy.py and the module comment on ToolDefinition).
        """
        return tuple(t for t in self._tools.values() if audience in t.audiences)

    def _schema(self, tool: ToolDefinition) -> dict:
        return {"type": "function", "function": {
            "name": tool.transport_name,
            "description": tool.description,
            "parameters": tool.arguments,
        }}

    def openai_tools_for_audience(self, audience: str) -> list[dict]:
        """The model-facing schema list for a given audience — the safe
        default for any caller building a tool list for an LLM. Prefer this
        over ``openai_tools_for_agent`` for model-facing use: that method's
        ``allowed_tools=None`` means "every registered tool", which is an easy
        footgun (a future internal/other-audience tool would be silently
        handed to whichever caller forgets to pass an allow-list)."""
        return [self._schema(tool) for tool in self.for_audience(audience)]

    def openai_tools_for_agent(self, allowed_tools=None) -> list[dict]:
        allowed = None if allowed_tools is None else set(allowed_tools)
        return [
            self._schema(tool)
            for tool in self._tools.values()
            if allowed is None or tool.name in allowed or tool.category in allowed
        ]
