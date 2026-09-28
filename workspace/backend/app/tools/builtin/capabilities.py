"""Generic, policy-enforced bridge from Operator to business capabilities."""


async def invoke(ctx, args):
    from app.capabilities import get_capability_router
    from app.capabilities.context import CapabilityExecutionContext
    from app.capabilities.contract import CapabilityRisk
    from app.capabilities.permissions import OPERATOR_PLATFORM_PERMISSIONS
    from app.capabilities.router import CapabilityNotFound
    from app.journey import JourneyService
    from app.memory.permissions import OPERATOR_CAPABILITIES
    from app.memory.student_context_gateway import StudentContextGateway
    from app.database import new_session
    from app.tools import AUDIENCE_OPERATOR, get_tool_registry

    capability_id = str(args.get("capability_id") or "").strip()
    payload = args.get("input")
    if not capability_id or not isinstance(payload, dict):
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "capability_id and object input are required"}}
    router = get_capability_router()
    try:
        contract = router.resolve(capability_id)
    except CapabilityNotFound as exc:
        return {"ok": False, "error": {"code": "capability_not_found", "message": str(exc)}}
    if contract.approval == "always" or contract.risk is CapabilityRisk.SENSITIVE:
        return {"ok": False, "error": {
            "code": "approval_required", "message": "Explicit student approval is required.",
            "pending_action": {"kind": "approval", "title": f"Approve {contract.name}",
                               "prompt": f"Do you approve {contract.name}?", "reason": contract.description,
                               "options": ["approve", "decline"], "required": True},
        }}

    db = new_session()
    try:
        scoped = StudentContextGateway(db).get(
            ctx.workspace_id, contract.vault_scopes, caller=ctx.agent_name,
            granted_permissions=OPERATOR_CAPABILITIES,
        ).to_dict()
        journey = JourneyService(db).resolve_primary(ctx.workspace_id)
        journey_data = journey.to_dict() if journey else {}
        missing = [field for field in contract.journey_fields if journey_data.get(field) is None]
        if missing:
            return {"ok": False, "error": {"code": "journey_context_missing", "message": f"missing journey field: {sorted(missing)[0]}"}}
        journey_context = {field: journey_data[field] for field in contract.journey_fields}
    finally:
        db.close()

    registry = get_tool_registry()
    tool_names = frozenset(tool.name for tool in registry.for_audience(AUDIENCE_OPERATOR))
    execution_context = CapabilityExecutionContext(
        workspace_id=ctx.workspace_id, student_context=scoped,
        journey_context=journey_context, permissions=OPERATOR_PLATFORM_PERMISSIONS,
        tool_names=tool_names,
    )
    try:
        result = await router.execute(capability_id, payload, execution_context)
    except PermissionError as exc:
        return {"ok": False, "error": {"code": "permission_denied", "message": str(exc)}}
    except Exception as exc:
        return {"ok": False, "error": {"code": "capability_failed", "message": str(exc)[:500]}}
    return {"ok": True, "data": {"capability_id": capability_id, "version": contract.version, "result": result}}
