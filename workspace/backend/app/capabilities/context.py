from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityExecutionContext:
    workspace_id: str
    student_context: dict[str, Any]
    journey_context: dict[str, Any]
    permissions: frozenset[str]
    tool_names: frozenset[str]
    tools: Any = None
