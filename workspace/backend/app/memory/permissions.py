# -*- coding: utf-8 -*-
"""Which PAI agent may do what to memory.

One table, read by both agents, different grants:

    PAI Counselor   memory.read  memory.manage  vault.read  vault.manage
    PAI Operator    memory.read                 vault.read

Operator can *consume* everything it needs to act on the student's behalf and
can change nothing. That is enforced by capability, not by tool name: a memory
tool added next month declaring `memory.manage` is withheld from Operator the
moment it is registered, with no edit here and no exclusion list in Operator.

`vault.manage` on Counselor is still "controlled" — it authorises the explicit
user commands ("remember X", "forget Y"), and those route through the same
deterministic reconciler as everything else. No agent writes Vault rows
directly.
"""

from app.tools.policy import Capability

# Imported rather than re-declared so a rename in one place cannot silently
# strip an agent's grant back to the read-only default.
from app.services.pai import PAI_AGENT_NAME as COUNSELOR_AGENT_NAME
from app.services.operator import PAI_OPERATOR_AGENT_NAME as OPERATOR_AGENT_NAME

COUNSELOR_CAPABILITIES = frozenset({
    Capability.MEMORY_READ.value,
    Capability.MEMORY_MANAGE.value,
    Capability.VAULT_READ.value,
    Capability.VAULT_MANAGE.value,
})

OPERATOR_CAPABILITIES = frozenset({
    Capability.MEMORY_READ.value,
    Capability.VAULT_READ.value,
})


def capabilities_for_agent(agent_name: str) -> frozenset[str]:
    """Capability grant for an agent.

    Unknown agents get the read-only grant. Defaulting closed matters: a future
    agent that nobody thought to add here cannot mutate the student's profile
    by accident.
    """
    if agent_name == COUNSELOR_AGENT_NAME:
        return COUNSELOR_CAPABILITIES
    if agent_name == OPERATOR_AGENT_NAME:
        return OPERATOR_CAPABILITIES
    return OPERATOR_CAPABILITIES
