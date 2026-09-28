from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

CapabilityHandler = Callable[[Any, dict], Awaitable[dict]]


class CapabilityRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class FallbackPolicy(str, Enum):
    FORBIDDEN = "forbidden"
    GENERIC_ALLOWED = "generic_allowed"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0


@dataclass(frozen=True)
class CapabilityContract:
    id: str
    version: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    handler: CapabilityHandler
    owns_task_types: frozenset[str] = field(default_factory=frozenset)
    fallback_policy: FallbackPolicy = FallbackPolicy.FORBIDDEN
    vault_scopes: frozenset[str] = field(default_factory=frozenset)
    journey_fields: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    required_tools: frozenset[str] = field(default_factory=frozenset)
    artifacts: frozenset[str] = field(default_factory=frozenset)
    risk: CapabilityRisk = CapabilityRisk.READ
    approval: str = "none"
    timeout_seconds: int = 60
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    evidence_expectations: dict[str, Any] = field(default_factory=dict)
    provider: str = "native"

    def __post_init__(self):
        object.__setattr__(self, "fallback_policy", FallbackPolicy(self.fallback_policy))
        object.__setattr__(self, "owns_task_types", frozenset(self.owns_task_types))
        object.__setattr__(self, "vault_scopes", frozenset(self.vault_scopes))
        object.__setattr__(self, "journey_fields", frozenset(self.journey_fields))
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(self, "required_tools", frozenset(self.required_tools))
