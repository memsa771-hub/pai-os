from abc import ABC, abstractmethod


class McpToolProvider(ABC):
    """Adapter boundary for importing external MCP tools into ToolRegistry."""

    @abstractmethod
    async def discover(self) -> list: ...

    @abstractmethod
    async def invoke(self, name: str, arguments: dict, context): ...
