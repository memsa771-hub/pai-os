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

    @property
    def source(self) -> str:
        return f"openagents:{self.agent_name}"
