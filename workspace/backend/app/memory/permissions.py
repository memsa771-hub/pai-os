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
    Capability.PROFILE_PROPOSE.value,
})


# An agent nobody has granted anything. Not a typo for "read-only": a student's
# profile is not public-by-default to whatever agent happens to join the
# workspace, and read access is itself a disclosure decision.
NO_CAPABILITIES: frozenset[str] = frozenset()

_GRANTS: dict[str, frozenset[str]] = {
    COUNSELOR_AGENT_NAME: COUNSELOR_CAPABILITIES,
    OPERATOR_AGENT_NAME: OPERATOR_CAPABILITIES,
}


def capabilities_for_agent(agent_name: str) -> frozenset[str]:
    """Capability grant for an agent. Unlisted agents get nothing.

    Fails closed on *reads* as well as writes. A third-party agent connected to
    the workspace — or a future PAI component nobody remembered to add here —
    cannot read the student's Vault, preferences or history until someone
    grants it explicitly.
    """
    return _GRANTS.get(agent_name, NO_CAPABILITIES)
