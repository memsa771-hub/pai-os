from app.tools.policy import ToolRisk
from app.tools.registry import ToolDefinition
from . import browser, files, tasks, web, workspace

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def obj(properties, required=()):
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def register_builtin_tools(registry):
    definitions = [
        ToolDefinition("workspace.agents.list", "List agents in this workspace and their status.", EMPTY, "workspace", ToolRisk.READ, workspace.list_agents),
        ToolDefinition("workspace.threads.list", "List workspace conversations.", EMPTY, "workspace", ToolRisk.READ, workspace.list_threads),
        ToolDefinition("workspace.thread.create", "Create a workspace conversation.", obj({"title": {"type": "string"}, "agents": {"type": "array", "items": {"type": "string"}}}, ["title"]), "workspace", ToolRisk.WRITE, workspace.create_thread),
        ToolDefinition("tasks.list", "List workspace task cards.", EMPTY, "tasks", ToolRisk.READ, tasks.list_tasks),
        ToolDefinition("tasks.create", "Create a task card requested by the user.", obj({"title": {"type": "string"}, "description": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "assignee": {"type": "string"}}, ["title"]), "tasks", ToolRisk.WRITE, tasks.create_task),
        ToolDefinition("files.list", "List files in workspace storage.", obj({"path": {"type": "string"}, "recursive": {"type": "boolean"}, "limit": {"type": "integer"}}), "files", ToolRisk.READ, files.list_files),
        ToolDefinition("files.read", "Read a text file from workspace storage by file ID.", obj({"file_id": {"type": "string"}, "max_chars": {"type": "integer"}}, ["file_id"]), "files", ToolRisk.READ, files.read_file),
        ToolDefinition("files.write", "Write UTF-8 text into workspace storage.", obj({"filename": {"type": "string"}, "content": {"type": "string"}, "content_type": {"type": "string"}}, ["filename", "content"]), "files", ToolRisk.WRITE, files.write_file),
        ToolDefinition("web.search", "Search the public web using the configured provider.", obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), "web", ToolRisk.READ, web.search),
        ToolDefinition("web.fetch", "Read a public URL through the workspace fetch and safety pipeline.", obj({"url": {"type": "string"}, "mode": {"type": "string", "enum": ["auto", "static", "render"]}, "max_chars": {"type": "integer"}}, ["url"]), "web", ToolRisk.READ, web.fetch),
        ToolDefinition("browser.tabs.list", "List shared workspace browser tabs.", EMPTY, "browser", ToolRisk.READ, browser.list_tabs),
        ToolDefinition("browser.open", "Open a shared browser tab, optionally in a persistent context.", obj({"url": {"type": "string"}, "context_id": {"type": "string"}}), "browser", ToolRisk.WRITE, browser.open_tab),
        ToolDefinition("browser.navigate", "Navigate an existing shared browser tab.", obj({"tab_id": {"type": "string"}, "url": {"type": "string"}}, ["tab_id", "url"]), "browser", ToolRisk.WRITE, browser.navigate),
        ToolDefinition("browser.read", "Read the accessibility snapshot of a shared browser tab.", obj({"tab_id": {"type": "string"}, "max_chars": {"type": "integer"}}, ["tab_id"]), "browser", ToolRisk.READ, browser.read),
        ToolDefinition("browser.click", "Click an element in a shared browser tab.", obj({"tab_id": {"type": "string"}, "selector": {"type": "string"}}, ["tab_id", "selector"]), "browser", ToolRisk.WRITE, browser.click),
        ToolDefinition("browser.type", "Type into an element in a shared browser tab.", obj({"tab_id": {"type": "string"}, "selector": {"type": "string"}, "text": {"type": "string"}, "append": {"type": "boolean"}}, ["tab_id", "selector", "text"]), "browser", ToolRisk.WRITE, browser.type_text),
        ToolDefinition("browser.screenshot", "Capture a shared browser tab screenshot as base64 PNG.", obj({"tab_id": {"type": "string"}}, ["tab_id"]), "browser", ToolRisk.READ, browser.screenshot),
        ToolDefinition("browser.close", "Close a shared browser tab.", obj({"tab_id": {"type": "string"}}, ["tab_id"]), "browser", ToolRisk.WRITE, browser.close),
        ToolDefinition("browser.contexts.list", "List persistent shared browser contexts.", EMPTY, "browser", ToolRisk.READ, browser.list_contexts),
    ]
    for definition in definitions:
        registry.register(definition)
