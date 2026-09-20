from app.tools.policy import Capability, ToolRisk
from app.tools.registry import AUDIENCE_COUNSELOR, AUDIENCE_OPERATOR, ToolDefinition
from . import browser, files, memory, operator, tasks, web, workspace

# Capability shorthands. Declared on the tool so a caller's grant decides
# access — see app/tools/policy.py and app/memory/permissions.py.
CAP_MEMORY_READ = frozenset({Capability.MEMORY_READ.value})
CAP_MEMORY_MANAGE = frozenset({Capability.MEMORY_MANAGE.value})
CAP_VAULT_READ = frozenset({Capability.VAULT_READ.value})
CAP_VAULT_MANAGE = frozenset({Capability.VAULT_MANAGE.value})
CAP_PROFILE_PROPOSE = frozenset({Capability.PROFILE_PROPOSE.value})

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
        ToolDefinition("workspace.threads.list", "List workspace conversations.", EMPTY, "workspace", ToolRisk.READ, workspace.list_threads, audiences=BOTH),
        ToolDefinition("tasks.list", "List workspace task cards.", EMPTY, "tasks", ToolRisk.READ, tasks.list_tasks, audiences=BOTH),
        ToolDefinition("files.list", "List files in workspace storage.", obj({"path": {"type": "string"}, "recursive": {"type": "boolean"}, "limit": {"type": "integer"}}), "files", ToolRisk.READ, files.list_files, audiences=BOTH),
        ToolDefinition("files.read", "Read a text file from workspace storage by file ID.", obj({"file_id": {"type": "string"}, "max_chars": {"type": "integer"}}, ["file_id"]), "files", ToolRisk.READ, files.read_file, audiences=BOTH),
        ToolDefinition("web.search", "Search the public web using the configured provider.", obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), "web", ToolRisk.READ, web.search, audiences=BOTH),
        ToolDefinition("web.fetch", "Read a public URL through the workspace fetch and safety pipeline.", obj({"url": {"type": "string"}, "mode": {"type": "string", "enum": ["auto", "static", "render"]}, "max_chars": {"type": "integer"}}, ["url"]), "web", ToolRisk.READ, web.fetch, audiences=BOTH),

        # ---- Real execution — Operator's domain only. Counselor delegates
        # instead of calling these; see operator.delegate below.
        # workspace.agents.list/workspace.thread.create are here too, even
        # though they're reads/lightweight — PAI does not let the student
        # manage agents or spin up threads directly (see PAI_SYSTEM_PROMPT in
        # app/services/pai.py), so Counselor has no business calling either. ----
        ToolDefinition("workspace.agents.list", "List agents in this workspace and their status.", EMPTY, "workspace", ToolRisk.READ, workspace.list_agents, audiences=OPERATOR_ONLY),
        ToolDefinition("workspace.thread.create", "Create a workspace conversation.", obj({"title": {"type": "string"}, "agents": {"type": "array", "items": {"type": "string"}}}, ["title"]), "workspace", ToolRisk.WRITE, workspace.create_thread, audiences=OPERATOR_ONLY),
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
                # The vocabulary has to be spelled out: unknown refs are dropped
                # silently (see MemoryContextService.build_student_context), so an
                # undocumented free-form array means Operator runs with no student
                # context and the objective carries a stale copy instead.
                "context_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which parts of the student's memory PAI Operator should "
                        "load, resolved live when the run executes. Pass these "
                        "whenever the objective depends on who the student is. "
                        "Valid values: \"vault\" (canonical profile facts — CGPA, "
                        "test scores, budget, preferred countries), \"memory\" "
                        "(preferences and goals; narrow with \"memory:preference\", "
                        "\"memory:goal\", \"memory:constraint\", \"memory:interest\"), "
                        "\"episodes\" (recent journey events). Anything else is "
                        "ignored. Prefer [\"vault\", \"memory\"] for profile-aware "
                        "work rather than restating the profile in the objective."
                    ),
                },
                "intent": {"type": "string", "enum": ["discovery", "academic_planning", "study_abroad_matching", "eligibility_analysis", "career_exploration", "scholarship_planning", "application_preparation", "application_execution", "visa_preparation", "enrollment", "document_review"]},
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

        # -- PAI Memory Platform ------------------------------------------
        # Read tools are audiences=BOTH and carry read capabilities, so PAI
        # Operator gets them. remember/forget are audiences=COUNSELOR_ONLY
        # (the explicit-user-command path; see app/tools/builtin/memory.py)
        # and carry manage capabilities, so Operator is blocked by both the
        # audience gate and the capability gate — see app/memory/permissions.py
        # for the grant table and ToolPolicy.authorize for how both are
        # enforced together.
        ToolDefinition(
            "memory.context",
            "Get what you know about this student — profile facts, education "
            "and career records, preferences and recent history. Use the context "
            "already injected; call this only for a specific missing/stale detail.",
            obj({
                "query": {"type": "string"},
                "context_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which sections to load; omit for all of them. Valid "
                        "values: \"vault\" (canonical profile facts), \"memory\" "
                        "(preferences/goals, narrow with \"memory:preference\", "
                        "\"memory:goal\", \"memory:constraint\", \"memory:interest\"), "
                        "\"episodes\" (recent journey events). Unknown values are "
                        "ignored."
                    ),
                },
                "intent": {"type": "string", "enum": ["discovery", "academic_planning", "study_abroad_matching", "eligibility_analysis", "career_exploration", "scholarship_planning", "application_preparation", "application_execution", "visa_preparation", "enrollment", "document_review"]},
            }),
            "memory", ToolRisk.READ, memory.get_context,
            capabilities=CAP_MEMORY_READ | CAP_VAULT_READ, audiences=BOTH,
        ),
        ToolDefinition(
            "vault.get",
            "Read the student's structured profile. Omit field_key for facts, "
            "repeatable records, issues and discovery readiness, "
            "snapshot, or pass one (e.g. 'education.cgpa') for that field with "
            "its provenance.",
            obj({"field_key": {"type": "string"}, "query": {"type": "string"}, "intent": {"type": "string"}}),
            "memory", ToolRisk.READ, memory.vault_get,
            capabilities=CAP_VAULT_READ, audiences=BOTH,
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
            capabilities=CAP_MEMORY_READ, audiences=BOTH,
        ),
        ToolDefinition(
            "memory.episodes",
            "List recent notable events in this student's journey.",
            obj({"event_type": {"type": "string"}, "limit": {"type": "integer"}}),
            "memory", ToolRisk.READ, memory.episodes_recent,
            capabilities=CAP_MEMORY_READ, audiences=BOTH,
        ),
        ToolDefinition(
            "profile.propose",
            "Propose structured student profile facts found during document or research work. "
            "Proposals are validated and reconciled by the backend; this never overwrites the profile directly.",
            obj({"proposals": {"type": "array", "maxItems": 50, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "candidate_type": {"type": "string", "enum": ["student_record", "vault_fact"]},
                    "key": {"type": "string"}, "value": {}, "entities": {"type": "object"},
                    "confidence": {"type": "number"}, "file_id": {"type": "string"},
                    "quote": {"type": "string"},
                }, "required": ["candidate_type", "key", "value"]
            }}}, ["proposals"]),
            "memory", ToolRisk.WRITE, memory.propose_profile,
            capabilities=CAP_PROFILE_PROPOSE, audiences=OPERATOR_ONLY,
        ),
        ToolDefinition(
            "memory.remember",
            "Durably record something the student explicitly asked you to "
            "remember. Pass field_key + value for a structured profile fact "
            "(e.g. 'finance.budget'), record_type + value for a repeatable record "
            "(also record_id to correct one returned by vault.get), or content "
            "for unstructured context. Record schemas are returned by vault.get. Use "
            "only for explicit instructions — ordinary conversation is captured "
            "automatically in the background.",
            obj({
                "content": {"type": "string"},
                "field_key": {"type": "string"},
                "record_type": {"type": "string"},
                "record_id": {"type": "string"},
                "value": {},
                "memory_type": {"type": "string", "enum": [
                    "preference", "goal", "constraint", "interest", "context",
                ]},
            }, ["content"]),
            "memory", ToolRisk.WRITE, memory.remember,
            capabilities=CAP_MEMORY_MANAGE | CAP_VAULT_MANAGE, audiences=COUNSELOR_ONLY,
        ),
        ToolDefinition(
            "memory.forget",
            "Stop using something the student asked you to forget. Pass a query "
            "to match preferences, or field_key to retract a profile fact.",
            obj({"query": {"type": "string"}, "field_key": {"type": "string"}}),
            "memory", ToolRisk.WRITE, memory.forget,
            capabilities=CAP_MEMORY_MANAGE | CAP_VAULT_MANAGE, audiences=COUNSELOR_ONLY,
        ),
    ]
    for definition in definitions:
        registry.register(definition)
