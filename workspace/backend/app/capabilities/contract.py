from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

CapabilityHandler = Callable[[Any, dict], Awaitable[dict]]


class CapabilityRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


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
