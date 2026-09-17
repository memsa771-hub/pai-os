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

    def openai_tools_for_audience(self, audience: str, granted_capabilities=None) -> list[dict]:
        """The model-facing schema list for a given audience — the safe
        default for any caller building a tool list for an LLM. Prefer this
        over ``openai_tools_for_agent`` for model-facing use: that method's
        ``allowed_tools=None`` means "every registered tool", which is an easy
        footgun (a future internal/other-audience tool would be silently
        handed to whichever caller forgets to pass an allow-list).

        ``granted_capabilities`` layers the same capability gate
        ``openai_tools_for_agent``/``ToolPolicy.authorize`` apply, so an
        audience-scoped caller (PAI Operator) is never shown a capability-
        gated tool (a Memory write) it holds no grant for, on top of never
        being shown a tool outside its audience."""
        return [
            self._schema(tool) for tool in self.for_audience(audience)
            if self.permits(tool, granted_capabilities)
        ]

    def openai_tools_for_agent(self, allowed_tools=None, granted_capabilities=None) -> list[dict]:
        """Tool schemas to advertise to a model.

        Filtered by the same two rules the executor enforces, so a caller is
        never shown a tool it would be refused — the model cannot be tempted
        by a tool it may not call.
        """
        allowed = None if allowed_tools is None else set(allowed_tools)
        return [
            self._schema(tool)
            for tool in self._tools.values()
            if (allowed is None or tool.name in allowed or tool.category in allowed)
            and self.permits(tool, granted_capabilities)
        ]

    @staticmethod
    def permits(tool: ToolDefinition, granted_capabilities=None) -> bool:
        """True if a caller holding `granted_capabilities` may use `tool`.

        Two rules, and they are not the same rule:

        * A tool that declares NO capabilities is unrestricted. Every
          pre-existing tool is in this class, so nothing that worked before
          capabilities existed is affected by them.
        * A tool that DOES declare capabilities **fails closed**: a caller with
          no grant (``None``) is refused, not waved through. ``None`` means
          "this caller never declared a grant", which for a privileged tool is
          exactly the case that must be denied.

        This must agree with ``ToolPolicy.authorize`` exactly — if the registry
        advertised a tool the policy then refused, a model would be handed a
        tool it cannot call and would waste turns discovering that.
        """
        required = tool.capabilities
        if not required:
            return True
        if not granted_capabilities:
            return False
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
