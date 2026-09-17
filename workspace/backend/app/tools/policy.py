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
        # Structural audience check (see ToolContext.audience) — independent
        # of `allowed_tools` on purpose: a caller built with allowed_tools=None
        # (no explicit allow-list) must still not reach a tool meant for a
        # different audience, so this can't be bypassed by simply forgetting
        # to pass an allow-list.
        audience = context.audience
        if audience is not None and audience not in tool.audiences:
            return False, f"Tool not available to this caller: {tool.name}"
        if tool.risk is ToolRisk.SENSITIVE and not self.allow_sensitive:
            return False, f"Tool requires approval: {tool.name}"
        return True, None
