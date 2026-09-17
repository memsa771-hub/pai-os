from .context import ToolContext
from .executor import ToolExecutor
from .policy import ToolPolicy, ToolRisk
from .registry import (
    AUDIENCE_COUNSELOR,
    AUDIENCE_INTERNAL,
    AUDIENCE_OPERATOR,
    ToolDefinition,
    ToolRegistry,
)

_registry = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        from .builtin import register_builtin_tools
        _registry = ToolRegistry()
        register_builtin_tools(_registry)
    return _registry


def get_tool_executor() -> ToolExecutor:
    return ToolExecutor(get_tool_registry())


__all__ = [
    "ToolContext", "ToolExecutor", "ToolPolicy", "ToolRisk", "ToolDefinition", "ToolRegistry",
    "get_tool_registry", "get_tool_executor",
    "AUDIENCE_COUNSELOR", "AUDIENCE_OPERATOR", "AUDIENCE_INTERNAL",
]
