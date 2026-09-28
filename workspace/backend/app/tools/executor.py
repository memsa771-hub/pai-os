import logging

from .policy import ToolPolicy
from .schema import SchemaValidationError, validate_schema_instance

# Compatibility import for Vault/record schema modules. The implementation
# lives only in tools.schema so tools and capabilities cannot drift.
_validate = validate_schema_instance

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, registry, policy: ToolPolicy | None = None):
        self.registry = registry
        self.policy = policy or ToolPolicy()

    async def execute(self, name: str, arguments: dict, context) -> dict:
        tool = self.registry.get(name)
        if not tool:
            return {"ok": False, "error": {"code": "unknown_tool", "message": "Unknown tool"}}
        allowed, reason = self.policy.authorize(tool, context)
        if not allowed:
            return {"ok": False, "error": {"code": "tool_not_allowed", "message": reason}}
        try:
            validate_schema_instance(tool.arguments, arguments, "arguments")
        except SchemaValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_arguments", "message": str(exc)}}
        try:
            result = await tool.handler(context, arguments)
            if isinstance(result, dict) and result.get("ok") is False:
                return result
            return result if isinstance(result, dict) and "ok" in result else {"ok": True, "data": result}
        except Exception as exc:
            logger.exception("tool execution failed name=%s workspace=%s agent=%s", tool.name, context.workspace_id, context.agent_name)
            return {"ok": False, "error": {"code": "execution_failed", "message": str(exc)[:200]}}
