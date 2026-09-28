# PAI Capability Developer Guide

## Core architecture

The student talks only to PAI Counselor. Counselor reads a scoped view of the canonical Student Vault, the active Student Journey, and a deterministic counseling decision. Substantial work is delegated to PAI Operator, which follows UNDERSTAND → PLAN → EXECUTE → OBSERVE → VERIFY. Operator discovers business capabilities through the Capability Router and uses the existing tool runtime for low-level actions.

## Vault and Journey

- **Vault** answers “what is true about this student?” It is the only canonical profile store. New information must become a candidate/proposal and pass validation, reconciliation, and conflict handling before it becomes canonical.
- **Journey** answers “where are we going, what are we doing now, and what happens next?” A student can have many lifetime journeys, with normally one active primary journey. Journey progress is not a Vault fact and must not duplicate the profile.

Use `StudentContextGateway` to request only the domains a task needs. Never query Vault ORM tables from a capability and never expose ORM objects as a contract.

## Counselor and Operator

Counselor is the sole student-facing intelligence. `CounselingEvaluator` and `CounselingPolicy` select the allowed move, personalization, roadmap, delegation, and question boundaries for each turn. The language model expresses that move naturally but must not override it.

Operator is domain-agnostic. Do not add branches such as `if university`, `if SOP`, or `if IELTS`. Register a capability instead. Human-required work pauses the same `ExecutionRun` with `pending_action`; `operator.resume` continues that row, retaining its plan, evidence, and completed tool history.

## Capability and Tool

A **capability** is a business ability such as `program.research`. A **tool** is a mechanism such as `web.search` or `files.write`. Capabilities may require tools; they are not interchangeable.

Every `CapabilityContract` declares:

- dotted id and semantic version;
- name, description, input schema, and output schema;
- required Vault scopes and Journey fields;
- platform permissions and required tools;
- possible artifacts, risk, and approval policy;
- timeout/retry behavior and evidence expectations.

Only first-party native Python providers are supported in Core v1. Do not load arbitrary third-party Python into the server process. The provider field leaves room for a future isolated MCP/remote implementation.

## Context, permissions, artifacts, and errors

Context access fails closed. Request only the scopes declared in the manifest: `identity`, `education`, `goals`, `preferences`, `finance`, `tests`, `skills`, `projects`, `achievements`, `documents`, or `applications`. The caller must hold the matching `vault.<scope>.read` permission. Sensitive or irreversible actions require explicit approval. Produced files/results must use a declared artifact type and remain attributable to their execution run.

Raise a clear typed error for invalid manifests, missing capabilities, denied permissions, invalid input, timeouts, and provider failure. Never turn an unverified result into success. Evidence-sensitive capabilities should return source identifiers/URLs and distinguish verified facts from gaps.

## Registering a capability

```python
from app.capabilities import CapabilityContract, CapabilityRisk, get_capability_registry

async def echo(context, payload):
    return {"text": payload["text"]}

get_capability_registry().register(CapabilityContract(
    id="example.echo",
    version="1.0.0",
    name="Example Echo",
    description="Testing-only capability that echoes text.",
    input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    output_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    handler=echo,
    vault_scopes=frozenset(),
    journey_fields=frozenset(),
    permissions=frozenset(),
    required_tools=frozenset(),
    artifacts=frozenset(),
    risk=CapabilityRisk.READ,
    approval="none",
    timeout_seconds=10,
))
```

Registration makes the id discoverable without editing Counselor, Operator, Vault, or Journey internals. Duplicate ids and invalid manifests are rejected.

## Testing requirements

Test manifest validation, duplicate rejection, routing by id, unknown ids, least-privilege context denial, output shape, permission denial, approval pauses, timeout/retry behavior, evidence, and failure paths. Prove the capability registers without changes to Operator. If it discovers student information, test that it submits a proposal and does not directly change Vault rows.

## Never allowed

- Create a second student profile or capability-owned profile.
- Write directly to Vault or typed student-record tables.
- Read arbitrary/full student context by default.
- Put domain logic in Counselor or Operator.
- Speak to the student from a capability or Operator.
- Bypass approval for sensitive or irreversible actions.
- Execute arbitrary third-party Python in the main process.
- Claim completion without verification and evidence.
