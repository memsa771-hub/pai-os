"""Model-safe discovery and policy-enforced capability invocation."""


def _summary(contract):
    return {
        "id": contract.id, "version": contract.version,
        "description": contract.description,
        "owned_task_types": sorted(contract.owns_task_types),
        "risk": contract.risk.value, "approval": contract.approval,
        "fallback_policy": contract.fallback_policy.value,
    }


async def list_capabilities(ctx, args):
    from app.capabilities import get_capability_registry

    return {"ok": True, "data": {"capabilities": [
        _summary(item) for item in get_capability_registry().all()
    ]}}


async def describe(ctx, args):
    from app.capabilities import get_capability_router
    from app.capabilities.router import CapabilityNotFound

    try:
        contract = get_capability_router().resolve(str(args.get("capability_id") or ""))
    except CapabilityNotFound as exc:
        return {"ok": False, "error": {"code": "capability_not_found", "message": str(exc)}}
    return {"ok": True, "data": {
        **_summary(contract), "name": contract.name,
        "input_schema": contract.input_schema, "output_schema": contract.output_schema,
        "vault_scopes": sorted(contract.vault_scopes),
        "journey_fields": sorted(contract.journey_fields),
        "required_tools": sorted(contract.required_tools),
        "permissions": sorted(contract.permissions),
        "artifacts": sorted(contract.artifacts),
        "evidence_expectations": contract.evidence_expectations,
    }}


async def invoke(ctx, args):
    from app.capabilities import get_capability_router
    from app.capabilities.context import CapabilityExecutionContext
    from app.capabilities.contract import CapabilityRisk
    from app.capabilities.permissions import OPERATOR_PLATFORM_PERMISSIONS
    from app.capabilities.router import CapabilityNotFound
    from app.capabilities.tool_broker import CapabilityToolBroker, CapabilityToolUnavailable
    from app.database import new_session
    from app.journey import JourneyService
    from app.memory.permissions import OPERATOR_CAPABILITIES
    from app.memory.student_context_gateway import StudentContextGateway
    from app.tools import get_tool_executor, get_tool_registry

    capability_id = str(args.get("capability_id") or "").strip()
    payload = args.get("input")
    if not capability_id or not isinstance(payload, dict):
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "capability_id and object input are required"}}
    required_owner = getattr(ctx, "required_capability_id", None)
    if required_owner and capability_id != required_owner:
        return {"ok": False, "error": {
            "code": "capability_owner_required",
            "message": f"task is owned by {required_owner}; another capability cannot replace it",
        }}
    router = get_capability_router()
    try:
        contract = router.resolve(capability_id)
    except CapabilityNotFound as exc:
        return {"ok": False, "error": {"code": "capability_not_found", "message": str(exc)}}
    approved = capability_id in getattr(ctx, "approved_capabilities", frozenset())
    if (contract.approval == "always" or contract.risk is CapabilityRisk.SENSITIVE) and not approved:
        return {"ok": False, "error": {
            "code": "approval_required", "message": "Explicit student approval is required.",
            "pending_action": {
                "kind": "approval", "title": f"Approve {contract.name}",
                "prompt": f"Do you approve {contract.name}?", "reason": contract.description,
                "options": ["approve", "decline"], "required": True,
                "purpose": "capability_invoke", "capability_id": capability_id,
            },
        }}

    db = new_session()
    try:
        scoped = StudentContextGateway(db).get(
            ctx.workspace_id, contract.vault_scopes, caller=ctx.agent_name,
            granted_permissions=OPERATOR_CAPABILITIES,
        ).to_dict()
        journey = JourneyService(db).resolve_active(ctx.workspace_id)
        journey_data = journey.to_dict() if journey else {}
        missing = [field for field in contract.journey_fields if journey_data.get(field) is None]
        if missing:
            return {"ok": False, "error": {"code": "journey_context_missing", "message": f"missing journey field: {sorted(missing)[0]}"}}
        journey_context = {field: journey_data[field] for field in contract.journey_fields}
    finally:
        db.close()

    try:
        broker = CapabilityToolBroker(
            contract=contract, registry=get_tool_registry(), executor=get_tool_executor(),
            host_context=ctx, platform_permissions=OPERATOR_PLATFORM_PERMISSIONS,
        )
    except CapabilityToolUnavailable as exc:
        return {"ok": False, "error": {"code": "required_tool_unavailable", "message": str(exc)}}
    execution_context = CapabilityExecutionContext(
        workspace_id=ctx.workspace_id, student_context=scoped,
        journey_context=journey_context, permissions=OPERATOR_PLATFORM_PERMISSIONS,
        tool_names=broker.names, tools=broker,
    )
    try:
        result = await router.execute(capability_id, payload, execution_context)
    except PermissionError as exc:
        return {"ok": False, "error": {"code": "permission_denied", "message": str(exc)}}
    except Exception as exc:
        return {"ok": False, "error": {"code": "capability_failed", "message": str(exc)[:500]}}
    return {"ok": True, "data": {"capability_id": capability_id, "version": contract.version, "result": result}}
