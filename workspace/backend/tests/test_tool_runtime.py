import asyncio

from app.tools import ToolContext, ToolDefinition, ToolExecutor, ToolRegistry, ToolRisk, get_tool_registry
from app.tools.web_search import WebSearchProvider, set_web_search_provider


class FakeApi:
    def __init__(self):
        self.calls = []

    async def get(self, path, actor=None, **params):
        self.actors = getattr(self, "actors", [])
        self.actors.append(actor)
        self.calls.append(("GET", path, params))
        return {"ok": True, "data": {"agents": [], "channels": [], "tabs": []}}

    async def post(self, path, json=None, actor=None):
        self.actors = getattr(self, "actors", [])
        self.actors.append(actor)
        self.calls.append(("POST", path, json))
        return {"ok": True, "data": {"title": "Example Domain", "content": "Example Domain"}}

    async def delete(self, path):
        self.calls.append(("DELETE", path, None))
        return {"ok": True, "data": {"status": "closed"}}

    async def get_text(self, path, max_chars=50000):
        self.calls.append(("TEXT", path, max_chars))
        return {"ok": True, "content": "page"}

    async def get_base64(self, path, max_bytes):
        self.calls.append(("B64", path, max_bytes))
        return {"ok": True, "content_base64": "cG5n"}


def context(allowed=None):
    return ToolContext("workspace-1", "pai", FakeApi(), conversation="thread-1", user_id="user-1", allowed_tools=None if allowed is None else frozenset(allowed))


def test_registry_registers_rejects_duplicates_and_builds_openai_schema():
    async def handler(_ctx, args):
        return args

    registry = ToolRegistry()
    tool = ToolDefinition("web.fetch", "Fetch", {"type": "object", "properties": {}}, "web", ToolRisk.READ, handler)
    registry.register(tool)
    schema = registry.openai_tools_for_agent(["web.fetch"])[0]
    assert schema["function"]["name"] == "web__fetch"
    assert schema["function"]["parameters"]["type"] == "object"
    try:
        registry.register(tool)
        assert False, "duplicate should fail"
    except ValueError:
        pass


def test_executor_validates_permissions_unknown_tools_and_failures():
    async def echo(_ctx, args):
        return {"value": args["value"]}

    async def broken(_ctx, _args):
        raise RuntimeError("safe failure")

    registry = ToolRegistry()
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
    registry.register(ToolDefinition("test.echo", "Echo", schema, "test", ToolRisk.READ, echo))
    registry.register(ToolDefinition("test.broken", "Break", {"type": "object", "properties": {}}, "test", ToolRisk.READ, broken))
    executor = ToolExecutor(registry)
    assert asyncio.run(executor.execute("test__echo", {"value": "ok"}, context(["test.echo"]))) == {"ok": True, "data": {"value": "ok"}}
    assert asyncio.run(executor.execute("test.echo", {}, context(["test.echo"]))) ["error"]["code"] == "invalid_arguments"
    assert asyncio.run(executor.execute("missing", {}, context()))["error"]["code"] == "unknown_tool"
    assert asyncio.run(executor.execute("test.echo", {"value": "x"}, context([])))["error"]["code"] == "tool_not_allowed"
    assert asyncio.run(executor.execute("test.broken", {}, context(["test.broken"])))["error"]["code"] == "execution_failed"


def test_builtin_registry_exposes_expected_capabilities():
    names = {tool.name for tool in get_tool_registry().all()}
    assert {"workspace.agents.list", "workspace.threads.list", "tasks.list", "tasks.create"} <= names
    assert {"web.search", "web.fetch", "files.list", "files.read", "files.write"} <= names
    assert {"browser.tabs.list", "browser.open", "browser.navigate", "browser.read", "browser.click", "browser.type", "browser.screenshot", "browser.close", "browser.contexts.list"} <= names


def test_web_fetch_uses_existing_authenticated_fetch_route():
    ctx = context(["web.fetch"])
    result = asyncio.run(ToolExecutor(get_tool_registry()).execute("web.fetch", {"url": "https://example.com"}, ctx))
    assert result["ok"]
    assert ctx.api.calls[0][1] == "/v1/fetch"
    assert ctx.api.calls[0][2]["network"] == "workspace-1"
    # Identity travels in the trusted header, never in the body.
    assert "source" not in ctx.api.calls[0][2]
    assert ctx.api.actors[0] == ctx.source


class MockSearch(WebSearchProvider):
    async def search(self, query, limit=5):
        return [{"title": "CS", "url": "https://example.edu", "snippet": query, "source": "mock"}][:limit]


def test_web_search_provider_and_missing_provider(monkeypatch):
    monkeypatch.setattr("app.tools.web_search.config.WEB_SEARCH_PROVIDER", "")
    monkeypatch.setattr("app.tools.web_search.config.WEB_SEARCH_API_KEY", "")
    set_web_search_provider(None)
    executor = ToolExecutor(get_tool_registry())
    missing = asyncio.run(executor.execute("web.search", {"query": "programs"}, context(["web.search"])))
    assert missing["error"]["code"] == "search_not_configured"
    set_web_search_provider(MockSearch())
    try:
        found = asyncio.run(executor.execute("web.search", {"query": "Germany"}, context(["web.search"])))
        assert found["ok"] and found["results"][0]["source"] == "mock"
    finally:
        set_web_search_provider(None)


def test_browser_tools_call_existing_browser_routes_without_credentials():
    ctx = context(["browser.tabs.list", "browser.navigate"])
    executor = ToolExecutor(get_tool_registry())
    asyncio.run(executor.execute("browser.tabs.list", {}, ctx))
    asyncio.run(executor.execute("browser.navigate", {"tab_id": "tab-1", "url": "https://example.com"}, ctx))
    assert ctx.api.calls[0][1] == "/v1/browser/tabs"
    assert ctx.api.calls[1][1] == "/v1/browser/tabs/tab-1/navigate"
    assert "key" not in repr(ctx.api.calls).lower()
