import asyncio

from .context import CapabilityExecutionContext
from .permissions import require_permissions
from .manifest import validate_instance


class CapabilityNotFound(LookupError):
    pass


class CapabilityRouter:
    """Resolves business ids without exposing package paths or providers."""
    def __init__(self, registry):
        self.registry = registry

    def resolve(self, capability_id: str):
        contract = self.registry.get(capability_id)
        if contract is None:
            raise CapabilityNotFound(f"unknown capability: {capability_id}")
        return contract

    def resolve_task(self, task_type: str | None):
        return self.registry.owner_for_task_type(task_type)

    async def execute(self, capability_id: str, payload: dict,
                      context: CapabilityExecutionContext) -> dict:
        contract = self.resolve(capability_id)
        require_permissions(contract.permissions, context.permissions)
        missing_tools = set(contract.required_tools) - set(context.tool_names)
        if missing_tools:
            raise PermissionError(f"required tool unavailable: {sorted(missing_tools)[0]}")
        validate_instance(contract.input_schema, payload, "capability input")
        last_error = None
        for attempt in range(contract.retry.max_attempts):
            try:
                result = await asyncio.wait_for(
                    contract.handler(context, payload), contract.timeout_seconds,
                )
                validate_instance(contract.output_schema, result, "capability output")
                return result
            except Exception as exc:
                last_error = exc
                if attempt + 1 < contract.retry.max_attempts and contract.retry.backoff_seconds:
                    await asyncio.sleep(contract.retry.backoff_seconds * (attempt + 1))
        raise last_error
