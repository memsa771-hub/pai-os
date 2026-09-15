import logging
from typing import Any

from .policy import ToolPolicy

logger = logging.getLogger(__name__)


def _validate(schema: dict, value: Any, path: str = "arguments") -> None:
    expected = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
    }
    if expected in checks and not checks[expected](value):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if expected == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"Missing required argument: {key}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(props)
            if unknown:
                raise ValueError(f"Unknown argument: {sorted(unknown)[0]}")
        for key, child in props.items():
            if key in value:
                _validate(child, value[key], f"{path}.{key}")
    if expected == "array" and "items" in schema:
        for index, item in enumerate(value):
            _validate(schema["items"], item, f"{path}[{index}]")


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
            _validate(tool.arguments, arguments)
        except ValueError as exc:
            return {"ok": False, "error": {"code": "invalid_arguments", "message": str(exc)}}
        try:
            result = await tool.handler(context, arguments)
            if isinstance(result, dict) and result.get("ok") is False:
                return result
            return result if isinstance(result, dict) and "ok" in result else {"ok": True, "data": result}
        except Exception as exc:
            logger.exception("tool execution failed name=%s workspace=%s agent=%s", tool.name, context.workspace_id, context.agent_name)
            return {"ok": False, "error": {"code": "execution_failed", "message": str(exc)[:200]}}
