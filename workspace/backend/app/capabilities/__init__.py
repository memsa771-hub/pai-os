"""Business capabilities above the low-level tool runtime."""

from .contract import CapabilityContract, CapabilityRisk, FallbackPolicy
from .registry import CapabilityRegistry
from .router import CapabilityRouter

_registry = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        from .loader import load_first_party_capabilities

        _registry = CapabilityRegistry()
        load_first_party_capabilities(_registry)
    return _registry


def get_capability_router() -> CapabilityRouter:
    return CapabilityRouter(get_capability_registry())

__all__ = [
    "CapabilityContract", "CapabilityRisk", "FallbackPolicy", "CapabilityRegistry", "CapabilityRouter",
    "get_capability_registry", "get_capability_router",
]
