from app.tools.policy import ToolRisk
from app.tools.registry import AUDIENCE_COUNSELOR, AUDIENCE_OPERATOR, ToolDefinition
from . import browser, files, operator, tasks, web, workspace

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

# Shorthand for the two audience combinations builtin tools actually use.
# Nothing here is registered as "internal" yet — that audience exists for a
# future debug/admin tool that must never be handed to either PAI Counselor
# or PAI Operator's model-facing tool list.
BOTH = frozenset({AUDIENCE_COUNSELOR, AUDIENCE_OPERATOR})
OPERATOR_ONLY = frozenset({AUDIENCE_OPERATOR})
COUNSELOR_ONLY = frozenset({AUDIENCE_COUNSELOR})


def obj(properties, required=()):
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def register_builtin_tools(registry):
    definitions = [
        # ---- Lightweight reads / context inspection — Counselor may use
        # these directly; Operator uses them too while executing a plan. ----
        ToolDefinition("workspace.agents.list", "List agents in this workspace and their status.", EMPTY, "workspace", ToolRisk.READ, workspace.list_agents, audiences=BOTH),
        ToolDefinition("workspace.threads.list", "List workspace conversations.", EMPTY, "workspace", ToolRisk.READ, workspace.list_threads, audiences=BOTH),
        ToolDefinition("workspace.thread.create", "Create a workspace conversation.", obj({"title": {"type": "string"}, "agents": {"type": "array", "items": {"type": "string"}}}, ["title"]), "workspace", ToolRisk.WRITE, workspace.create_thread, audiences=BOTH),
        ToolDefinition("tasks.list", "List workspace task cards.", EMPTY, "tasks", ToolRisk.READ, tasks.list_tasks, audiences=BOTH),
        ToolDefinition("files.list", "List files in workspace storage.", obj({"path": {"type": "string"}, "recursive": {"type": "boolean"}, "limit": {"type": "integer"}}), "files", ToolRisk.READ, files.list_files, audiences=BOTH),
        ToolDefinition("files.read", "Read a text file from workspace storage by file ID.", obj({"file_id": {"type": "string"}, "max_chars": {"type": "integer"}}, ["file_id"]), "files", ToolRisk.READ, files.read_file, audiences=BOTH),
        ToolDefinition("web.search", "Search the public web using the configured provider.", obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), "web", ToolRisk.READ, web.search, audiences=BOTH),
        ToolDefinition("web.fetch", "Read a public URL through the workspace fetch and safety pipeline.", obj({"url": {"type": "string"}, "mode": {"type": "string", "enum": ["auto", "static", "render"]}, "max_chars": {"type": "integer"}}, ["url"]), "web", ToolRisk.READ, web.fetch, audiences=BOTH),

        # ---- Real execution — Operator's domain only. Counselor delegates
        # instead of calling these; see operator.delegate below. ----
        ToolDefinition("tasks.create", "Create a task card requested by the user.", obj({"title": {"type": "string"}, "description": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "assignee": {"type": "string"}}, ["title"]), "tasks", ToolRisk.WRITE, tasks.create_task, audiences=OPERATOR_ONLY),
        ToolDefinition("files.write", "Write UTF-8 text into workspace storage.", obj({"filename": {"type": "string"}, "content": {"type": "string"}, "content_type": {"type": "string"}}, ["filename", "content"]), "files", ToolRisk.WRITE, files.write_file, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.tabs.list", "List shared workspace browser tabs.", EMPTY, "browser", ToolRisk.READ, browser.list_tabs, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.open", "Open a shared browser tab, optionally in a persistent context.", obj({"url": {"type": "string"}, "context_id": {"type": "string"}}), "browser", ToolRisk.WRITE, browser.open_tab, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.navigate", "Navigate an existing shared browser tab.", obj({"tab_id": {"type": "string"}, "url": {"type": "string"}}, ["tab_id", "url"]), "browser", ToolRisk.WRITE, browser.navigate, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.read", "Read the accessibility snapshot of a shared browser tab.", obj({"tab_id": {"type": "string"}, "max_chars": {"type": "integer"}}, ["tab_id"]), "browser", ToolRisk.READ, browser.read, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.click", "Click an element in a shared browser tab.", obj({"tab_id": {"type": "string"}, "selector": {"type": "string"}}, ["tab_id", "selector"]), "browser", ToolRisk.WRITE, browser.click, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.type", "Type into an element in a shared browser tab.", obj({"tab_id": {"type": "string"}, "selector": {"type": "string"}, "text": {"type": "string"}, "append": {"type": "boolean"}}, ["tab_id", "selector", "text"]), "browser", ToolRisk.WRITE, browser.type_text, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.screenshot", "Capture a shared browser tab screenshot as base64 PNG.", obj({"tab_id": {"type": "string"}}, ["tab_id"]), "browser", ToolRisk.READ, browser.screenshot, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.close", "Close a shared browser tab.", obj({"tab_id": {"type": "string"}}, ["tab_id"]), "browser", ToolRisk.WRITE, browser.close, audiences=OPERATOR_ONLY),
        ToolDefinition("browser.contexts.list", "List persistent shared browser contexts.", EMPTY, "browser", ToolRisk.READ, browser.list_contexts, audiences=OPERATOR_ONLY),

        # ---- The Counselor <-> Operator boundary itself. ----
        ToolDefinition(
            "operator.delegate",
            "Delegate a concrete objective to PAI Operator, the internal execution "
            "intelligence, to carry out in the background using the available tools "
            "(browser, docs, tasks, workflows, web search). Returns immediately with "
            "a run_id — use operator.status to check progress later. Use this for "
            "multi-step work (research, filling a draft, checking documents), not "
            "for a single quick lookup you can do yourself with one tool call.",
            obj({
                "objective": {"type": "string"},
                "constraints": {"type": "object", "additionalProperties": True},
                "context_refs": {"type": "array", "items": {"type": "string"}},
            }, ["objective"]),
            "operator", ToolRisk.WRITE, operator.delegate, audiences=COUNSELOR_ONLY,
        ),
        ToolDefinition(
            "operator.status",
            "Check the status of a PAI Operator run — what it has done, what's "
            "missing, and whether it is waiting on your approval. Omit run_id for "
            "the most recent run in this workspace.",
            obj({"run_id": {"type": "string"}}),
            "operator", ToolRisk.READ, operator.status, audiences=COUNSELOR_ONLY,
        ),
    ]
    for definition in definitions:
        registry.register(definition)
