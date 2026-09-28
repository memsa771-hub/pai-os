"""Domain-agnostic per-run capability ownership and Operator tool policy."""

from dataclasses import dataclass

from .contract import CapabilityContract, FallbackPolicy

CAPABILITY_ORCHESTRATION_TOOLS = frozenset({
    "capability.list", "capability.describe", "capability.invoke",
    "profile.propose",
})


@dataclass(frozen=True)
class RunCapabilityPolicy:
    task_type: str | None
    owner: CapabilityContract | None
    generic_fallback_approved: bool = False

    @property
    def owns_domain(self) -> bool:
        return self.owner is not None

    @property
    def restrict_raw_tools(self) -> bool:
        return self.owner is not None and not self.generic_fallback_approved

    @property
    def fallback_policy(self) -> FallbackPolicy:
        return self.owner.fallback_policy if self.owner else FallbackPolicy.GENERIC_ALLOWED

    def allowed_tools(self, available: frozenset[str]) -> frozenset[str]:
        if not self.restrict_raw_tools:
            return available
        return available & CAPABILITY_ORCHESTRATION_TOOLS


def resolve_run_policy(registry, task_type: str | None, resume_input: dict | None = None) -> RunCapabilityPolicy:
    owner = registry.owner_for_task_type(task_type)
    pending = (resume_input or {}).get("pending_action") or {}
    response = (resume_input or {}).get("response") or {}
    fallback_approved = bool(
        pending.get("purpose") == "capability_fallback"
        and response.get("approved") is True
    )
    return RunCapabilityPolicy(task_type, owner, fallback_approved)
