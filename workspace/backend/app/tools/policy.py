from dataclasses import dataclass
from enum import Enum


class ToolRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


@dataclass(frozen=True)
class ToolPolicy:
    """Capability-driven authorization hook for the shared tool runtime."""

    allow_sensitive: bool = False

    def authorize(self, tool, context) -> tuple[bool, str | None]:
        allowed = context.allowed_tools
        if allowed is not None and tool.name not in allowed and tool.category not in allowed:
            return False, f"Tool not allowed: {tool.name}"
        if tool.risk is ToolRisk.SENSITIVE and not self.allow_sensitive:
            return False, f"Tool requires approval: {tool.name}"
        return True, None
