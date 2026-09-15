from abc import ABC, abstractmethod


class LocalToolProvider(ABC):
    """Future bridge to the existing local daemon; exposes nothing by default."""

    @abstractmethod
    async def available_tools(self) -> list: ...

    @abstractmethod
    async def invoke(self, name: str, arguments: dict, context): ...
