"""Business capabilities above the low-level tool runtime."""

from .contract import CapabilityContract, CapabilityRisk
from .registry import CapabilityRegistry
from .router import CapabilityRouter

_registry = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry


def get_capability_router() -> CapabilityRouter:
    return CapabilityRouter(get_capability_registry())

__all__ = [
    "CapabilityContract", "CapabilityRisk", "CapabilityRegistry", "CapabilityRouter",
    "get_capability_registry", "get_capability_router",
]
