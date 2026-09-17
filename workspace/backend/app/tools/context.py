from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ToolContext:
    """Authoritative execution scope created by the host, never model input."""

    workspace_id: str
    agent_name: str
    api: Any
    agent_id: Optional[str] = None
    conversation: Optional[str] = None
    user_id: Optional[str] = None
    allowed_tools: Optional[frozenset[str]] = field(default=None)
    # Who is actually calling ("counselor"/"operator" — see AUDIENCE_* in
    # tools/registry.py). ToolPolicy checks this against ToolDefinition.audiences
    # at execution time, independent of `allowed_tools` — a second, structural
    # layer so a caller that's merely missing an allowed_tools entry (or one
    # constructed with allowed_tools=None) still can't reach a tool meant for
    # the other audience. None skips the check (non-PAI/internal callers).
    audience: Optional[str] = field(default=None)
    # Capabilities this caller holds. ``None`` means "unrestricted" (the
    # historical behaviour, and what every pre-memory caller still gets); a
    # frozenset means the caller may only use tools whose declared
    # capabilities are a subset of it. See app/tools/policy.py:Capability.
    granted_capabilities: Optional[frozenset[str]] = field(default=None)

    @property
    def source(self) -> str:
        return f"openagents:{self.agent_name}"
