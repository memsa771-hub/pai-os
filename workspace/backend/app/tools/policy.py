from dataclasses import dataclass
from enum import Enum


class ToolRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class Capability(str, Enum):
    """What a tool *needs*, declared by the tool rather than by its callers.

    A caller (PAI Counselor, PAI Operator, a future agent) is granted a set of
    capabilities; a tool is usable by that caller only if every capability the
    tool declares is in the caller's grant. This inverts the old "allow
    everything except a name prefix" rule: a new tool that declares
    ``MEMORY_MANAGE`` is excluded from Operator the moment it is registered,
    with no edit to Operator and no name-based exclusion list anywhere.

    Tools that declare no capabilities are unrestricted — they are governed by
    ``ToolContext.allowed_tools`` and ``ToolRisk`` exactly as before, so every
    pre-existing tool keeps its current behaviour.
    """

    MEMORY_READ = "memory.read"
    MEMORY_MANAGE = "memory.manage"
    VAULT_READ = "vault.read"
    VAULT_MANAGE = "vault.manage"


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
        # Capability gate. A tool declaring no capabilities is unrestricted
        # (every pre-capability tool); one that declares them fails closed for
        # a caller with no grant. The decision itself lives in
        # ToolRegistry.permits so advertising and enforcement cannot disagree —
        # this branch only turns the same answer into a reason string.
        required = getattr(tool, "capabilities", frozenset())
        if required:
            from .registry import ToolRegistry

            granted = context.granted_capabilities
            if not ToolRegistry.permits(tool, granted):
                if not granted:
                    return False, f"Tool requires capabilities: {tool.name}"
                missing = sorted(set(required) - set(granted))
                return False, (
                    f"Tool requires capability {missing[0]}: {tool.name}"
                )
        if tool.risk is ToolRisk.SENSITIVE and not self.allow_sensitive:
            return False, f"Tool requires approval: {tool.name}"
        return True, None
