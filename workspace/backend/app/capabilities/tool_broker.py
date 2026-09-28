"""Least-privilege capability access to the shared PAI tool runtime."""

from app.tools import AUDIENCE_OPERATOR, ToolContext


class CapabilityToolUnavailable(PermissionError):
    pass


def permission_for_tool(name: str) -> str:
    if name == "web.search" or name == "web.fetch":
        return "web.read"
    if name == "files.read" or name == "files.list":
        return "files.read"
    if name == "files.write":
        return "files.write"
    if name in {"browser.tabs.list", "browser.contexts.list", "browser.read", "browser.screenshot"}:
        return "browser.read"
    if name.startswith("browser."):
        return "browser.write"
    if name == "profile.propose":
        return "profile.propose"
    return name


class CapabilityToolBroker:
    """Only supported low-level tool path for capability implementations."""

    def __init__(self, *, contract, registry, executor, host_context,
                 platform_permissions: frozenset[str]):
        self._contract = contract
        self._registry = registry
        self._executor = executor
        self._tool_names = frozenset(contract.required_tools)
        self._context = ToolContext(
            workspace_id=host_context.workspace_id,
            agent_name=host_context.agent_name,
            api=host_context.api,
            agent_id=host_context.agent_id,
            conversation=host_context.conversation,
            user_id=host_context.user_id,
            allowed_tools=self._tool_names,
            audience=AUDIENCE_OPERATOR,
            granted_capabilities=host_context.granted_capabilities,
        )
        for name in self._tool_names:
            tool = registry.get(name)
            if tool is None or AUDIENCE_OPERATOR not in tool.audiences:
                raise CapabilityToolUnavailable(f"required tool unavailable: {name}")
            if not registry.permits(tool, host_context.granted_capabilities):
                raise CapabilityToolUnavailable(f"required tool not permitted: {name}")
            permission = permission_for_tool(name)
            if permission not in contract.permissions or permission not in platform_permissions:
                raise CapabilityToolUnavailable(f"required tool permission denied: {permission}")

    @property
    def names(self) -> frozenset[str]:
        return self._tool_names

    async def invoke(self, name: str, arguments: dict) -> dict:
        if name not in self._tool_names:
            raise CapabilityToolUnavailable(f"undeclared capability tool: {name}")
        # ToolExecutor performs recursive schema validation and ToolPolicy
        # authorization. The broker never imports a builtin implementation.
        return await self._executor.execute(name, arguments, self._context)
