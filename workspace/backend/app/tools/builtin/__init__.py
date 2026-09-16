from app.tools.policy import Capability, ToolRisk
from app.tools.registry import ToolDefinition
from . import browser, files, memory, operator, tasks, web, workspace

# Capability shorthands. Declared on the tool so a caller's grant decides
# access — see app/tools/policy.py and app/memory/permissions.py.
CAP_MEMORY_READ = frozenset({Capability.MEMORY_READ.value})
CAP_MEMORY_MANAGE = frozenset({Capability.MEMORY_MANAGE.value})
CAP_VAULT_READ = frozenset({Capability.VAULT_READ.value})
CAP_VAULT_MANAGE = frozenset({Capability.VAULT_MANAGE.value})

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
            "operator", ToolRisk.WRITE, operator.delegate,
        ),
        ToolDefinition(
            "operator.status",
            "Check the status of a PAI Operator run — what it has done, what's "
            "missing, and whether it is waiting on your approval. Omit run_id for "
            "the most recent run in this workspace.",
            obj({"run_id": {"type": "string"}}),
            "operator", ToolRisk.READ, operator.status,
        ),

        # -- PAI Memory Platform ------------------------------------------
        # Read tools carry read capabilities, so PAI Operator gets them.
        # remember/forget carry manage capabilities, so it does not — and a
        # memory tool added later is governed the same way with no edit to
        # Operator.
        ToolDefinition(
            "memory.context",
            "Get what you know about this student — profile facts, preferences "
            "and recent history — as a compact context block. Call this before "
            "advising, rather than asking the student to repeat themselves.",
            obj({
                "query": {"type": "string"},
                "context_refs": {"type": "array", "items": {"type": "string"}},
            }),
            "memory", ToolRisk.READ, memory.get_context,
            capabilities=CAP_MEMORY_READ | CAP_VAULT_READ,
        ),
        ToolDefinition(
            "vault.get",
            "Read the student's structured profile. Omit field_key for the full "
            "snapshot, or pass one (e.g. 'education.cgpa') for that field with "
            "its provenance.",
            obj({"field_key": {"type": "string"}}),
            "memory", ToolRisk.READ, memory.vault_get,
            capabilities=CAP_VAULT_READ,
        ),
        ToolDefinition(
            "memory.search",
            "Search the student's long-term preferences, goals and constraints.",
            obj({
                "query": {"type": "string"},
                "memory_type": {"type": "string", "enum": [
                    "preference", "goal", "constraint", "interest", "context",
                ]},
                "limit": {"type": "integer"},
            }, ["query"]),
            "memory", ToolRisk.READ, memory.memory_search,
            capabilities=CAP_MEMORY_READ,
        ),
        ToolDefinition(
            "memory.episodes",
            "List recent notable events in this student's journey.",
            obj({"event_type": {"type": "string"}, "limit": {"type": "integer"}}),
            "memory", ToolRisk.READ, memory.episodes_recent,
            capabilities=CAP_MEMORY_READ,
        ),
        ToolDefinition(
            "memory.remember",
            "Durably record something the student explicitly asked you to "
            "remember. Pass field_key + value for a structured profile fact "
            "(e.g. 'finance.budget'), or content for a preference or goal. Use "
            "only for explicit instructions — ordinary conversation is captured "
            "automatically in the background.",
            obj({
                "content": {"type": "string"},
                "field_key": {"type": "string"},
                "value": {},
                "memory_type": {"type": "string", "enum": [
                    "preference", "goal", "constraint", "interest", "context",
                ]},
            }, ["content"]),
            "memory", ToolRisk.WRITE, memory.remember,
            capabilities=CAP_MEMORY_MANAGE | CAP_VAULT_MANAGE,
        ),
        ToolDefinition(
            "memory.forget",
            "Stop using something the student asked you to forget. Pass a query "
            "to match preferences, or field_key to retract a profile fact.",
            obj({"query": {"type": "string"}, "field_key": {"type": "string"}}),
            "memory", ToolRisk.WRITE, memory.forget,
            capabilities=CAP_MEMORY_MANAGE | CAP_VAULT_MANAGE,
        ),
    ]
    for definition in definitions:
        registry.register(definition)
