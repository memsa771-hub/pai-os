# -*- coding: utf-8 -*-
"""PAI Memory Platform — one shared memory service for every PAI agent.

    Conversation / document / action
            |
      MemoryCandidate         (LLMs may only propose)
            |
      MemoryReconciler        (deterministic; decides what becomes true)
            |
    Vault / Semantic / Episodes
            |
      MemoryContextService    (the single read path for prompts)
          /            \
  PAI Counselor      PAI Operator

Counselor and Operator share these tables. They differ only in the
capabilities their tool grants carry — see app/tools/policy.py and
app/memory/permissions.py. There is no second memory store for Operator.
"""

from .permissions import (
    COUNSELOR_CAPABILITIES,
    NO_CAPABILITIES,
    OPERATOR_CAPABILITIES,
    capabilities_for_agent,
)

__all__ = [
    "COUNSELOR_CAPABILITIES",
    "NO_CAPABILITIES",
    "OPERATOR_CAPABILITIES",
    "capabilities_for_agent",
]
