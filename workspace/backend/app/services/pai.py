# -*- coding: utf-8 -*-
"""
PAI Counselor — Placement AI's built-in education counselor.
PAI Counselor is a *cloud agent* (runs in-process on the backend, see
``services/cloud_agent.py``) but differs from user-added cloud agents in three
ways:

1. It is **auto-provisioned** into every workspace (see ``provision_pai``),
   rather than added by hand via ``POST /v1/cloud-agents``.
2. Its credentials are **server-held** and shared across all workspaces
   (``config.PAI_*``) — never entered by the user, never persisted per
   workspace (the ``api_key`` column stores only a placeholder).
3. It runs a **tool-calling loop** (category ``"assistant"``) instead of a
   single chat round-trip, so it can actually help the user set things up.

Tool calls go through the REAL workspace HTTP API via an in-process ASGI
client (``WorkspaceApi``) — never direct DB queries — so auth, validation,
serialization, and side effects (SSE publish, background tasks) behave exactly
as they do for any other client.

The built-in identity is the reserved provider ``"placement_ai"`` — that is what
``builtin`` is derived from everywhere (discover/workspace serializers,
frontend gating).
"""

import logging
import base64
from typing import Any, Optional

import httpx
from sqlalchemy import select

from app.config import config
from app.models import CloudAgentConfig, WorkspaceMember

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Identity constants — the single source of truth for "what is the built-in".
# ---------------------------------------------------------------------------

PAI_AGENT_NAME = "pai"
PAI_PROVIDER = "placement_ai"
PAI_AGENT_TYPE = f"cloud:{PAI_PROVIDER}"     # stored on the WorkspaceMember row
PAI_CATEGORY = "assistant"                    # triggers the tool loop
# Placeholder stored in CloudAgentConfig.api_key (NOT NULL). The real key is
# resolved from config at call time so it can be rotated in one place.
PAI_KEY_PLACEHOLDER = "__server_managed__"
PAI_PRIMARY_CHANNEL = "pai-counselor"
PAI_ALLOWED_TOOLS = (
    "workspace.agents.list", "workspace.threads.list", "workspace.thread.create",
    "tasks.list", "tasks.create", "files.list", "files.read", "files.write",
    "web.search", "web.fetch", "browser.tabs.list", "browser.open",
    "browser.navigate", "browser.read", "browser.click", "browser.type",
    "browser.screenshot", "browser.close", "browser.contexts.list",
)


def is_builtin_agent_type(agent_type: Optional[str]) -> bool:
    """True if a WorkspaceMember.agent_type belongs to the built-in assistant."""
    return (agent_type or "") == PAI_AGENT_TYPE


def resolve_credentials(cloud_config: CloudAgentConfig) -> tuple[str, Optional[str]]:
    """Return (api_key, base_url) to use for a cloud agent.

    For the built-in ``placement_ai`` provider the key/base_url come from
    server config (shared, rotatable), NOT from the stored row.
    """
    if cloud_config.provider == PAI_PROVIDER:
        return config.PAI_API_KEY, (config.PAI_BASE_URL or None)
    return cloud_config.api_key, cloud_config.base_url


def resolve_model(cloud_config: CloudAgentConfig) -> str:
    """The model to run a cloud agent with.

    Built-in PAI Counselor is server-managed end to end: the model comes from
    ``config.PAI_MODEL`` at call time (like the key), NOT from the row
    persisted at provision time — so one env/config change switches every
    workspace's PAI Counselor at the next deploy, with no backfill.
    """
    if cloud_config.provider == PAI_PROVIDER and config.PAI_MODEL:
        return config.PAI_MODEL
    return cloud_config.model


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def should_provision() -> bool:
    """Only seed PAI Counselor when enabled AND a server key is configured, so
    self-hosted deployments without a key don't get a broken agent."""
    return bool(config.PAI_ENABLED and config.PAI_API_KEY)


def validate_config() -> bool:
    """Validate PAI's server-side runtime configuration without exposing it."""
    if config.PAI_ENABLED and not config.PAI_API_KEY:
        logger.error("PAI Counselor is enabled but PAI_API_KEY is not configured.")
        return False
    return True


def provision_pai(db, workspace) -> bool:
    """Idempotently add the built-in PAI Counselor agent to a workspace.

    Creates the ``WorkspaceMember`` + ``CloudAgentConfig`` rows (mirroring
    ``POST /v1/cloud-agents``) if PAI Counselor isn't already present. Caller is
    responsible for committing. Returns True if a row was added.

    NOTE: does not run when ``should_provision()`` is False.
    """
    if not should_provision():
        return False

    workspace_id = str(workspace.id)

    # Namespace lock BEFORE the reads, so a concurrent rename/join can't
    # invalidate what we read here before we write.
    from app import naming
    naming.lock_member_namespace(db, workspace.id)

    existing_member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == PAI_AGENT_NAME,
        )
    ).scalar_one_or_none()

    existing_cfg = db.execute(
        select(CloudAgentConfig).where(
            CloudAgentConfig.workspace_id == workspace_id,
            CloudAgentConfig.agent_name == PAI_AGENT_NAME,
        )
    ).scalar_one_or_none()

    # A live PAI Counselor already exists — nothing to do. (If the user removed PAI Counselor it's
    # a hard delete, so both rows are gone and we'd re-provision; that's only on
    # explicit re-add, not here.)
    if existing_member and existing_member.status != "removed" and existing_cfg:
        return False

    # The name may be taken by a REAL agent (a user's daemon that happens to
    # be called "pai"). Backfilling would rewrite its agent_type/description
    # and attach the built-in config — a takeover, not a repair. Only a member
    # that already is the built-in type may be repaired; a removed real agent
    # counts too, since backfill would resurrect it as the built-in.
    if existing_member and (existing_member.agent_type or "") != PAI_AGENT_TYPE:
        logger.warning(
            "pai: skipped backfill in %s — a %s agent (status=%s) already "
            "owns the name",
            workspace_id, existing_member.agent_type, existing_member.status,
        )
        return False

    # Namespace guard runs BEFORE any session mutation: the backfill loop
    # shares one session across workspaces, so bailing out after a db.add()
    # would leave an orphan pending object that the next workspace's commit
    # persists.
    if existing_member is None:
        alias_clash = naming.find_alias_clash(
            db, workspace_id, PAI_AGENT_NAME, exclude_agent=PAI_AGENT_NAME,
        )
        if alias_clash:
            logger.warning(
                "pai: skipped backfill in %s — name clashes with display "
                "name of member %s", workspace_id, alias_clash,
            )
            return False

    if existing_cfg is None:
        db.add(CloudAgentConfig(
            workspace_id=workspace_id,
            agent_name=PAI_AGENT_NAME,
            provider=PAI_PROVIDER,
            model=config.PAI_MODEL,
            category=PAI_CATEGORY,
            api_key=PAI_KEY_PLACEHOLDER,
            base_url=None,
            system_prompt=None,
            max_tokens=None,
        ))
    else:
        existing_cfg.provider = PAI_PROVIDER
        existing_cfg.model = config.PAI_MODEL
        existing_cfg.category = PAI_CATEGORY
        existing_cfg.api_key = PAI_KEY_PLACEHOLDER
        existing_cfg.status = "active"

    description = "Placement AI's primary education counselor"
    if existing_member is None:
        db.add(WorkspaceMember(
            workspace_id=workspace.id,
            agent_name=PAI_AGENT_NAME,
            role="member",
            agent_type=PAI_AGENT_TYPE,
            status="online",
            description=description,
            display_name="PAI Counselor",
        ))
    else:
        existing_member.status = "online"
        existing_member.last_heartbeat = None
        existing_member.agent_type = PAI_AGENT_TYPE
        existing_member.description = description
        existing_member.display_name = "PAI Counselor"

    logger.info("pai: provisioned built-in assistant in workspace %s", workspace_id)
    return True


# Tappable prompts shown under the seeded greeting (frontend renders
# ``metadata.suggestions`` as one-tap chips — typing on a phone is the most
# expensive thing we can ask of a brand-new user).
WELCOME_SUGGESTIONS = [
    "Help me plan my education journey",
    "Help me explore my options",
    "What should I do next?",
]

WELCOME_GREETING = (
    "Hi, I'm PAI Counselor — your personal education counselor inside Placement AI.\n\n"
    "Tell me where you are in your education journey, or what you want to achieve, "
    "and I'll help you figure out the best next step."
)


def ensure_primary_conversation(db, workspace) -> bool:
    """Idempotently create the workspace's canonical PAI conversation.

    Identity-created workspaces get zero channels and PAI Counselor only ever *reacts*,
    so a brand-new user lands in an empty room — a dead end on mobile, where
    the launcher can't be installed. Seed one PAI Counselor-led thread with a greeting
    and tappable suggestions so the first minute delivers a working agent
    conversation instead. Caller commits; call inside try/except — this must
    never block workspace creation.
    """
    import time
    import uuid

    from app.models import Channel, ChannelMember, EventRecord, Workspace

    # Serialize first-time creation on PostgreSQL so simultaneous discovery
    # requests cannot race between the existence check and insert.
    db.execute(
        select(Workspace.id)
        .where(Workspace.id == workspace.id)
        .with_for_update()
    ).scalar_one()

    # Only ever for a truly fresh workspace — if any channel exists we're not
    # first-run (re-provisioning, backfill, agent-created workspace…).
    has_channel = db.execute(
        select(Channel.id).where(
            Channel.workspace_id == workspace.id,
            Channel.name == PAI_PRIMARY_CHANNEL,
        ).limit(1)
    ).scalar_one_or_none()
    if has_channel:
        existing = db.execute(
            select(Channel).where(Channel.id == has_channel)
        ).scalar_one()
        if existing.status != "active":
            existing.status = "active"
            existing.title = "PAI Counselor"
            existing.master_agent = PAI_AGENT_NAME
            db.flush()
            return True
        return False

    channel = Channel(
        workspace_id=workspace.id,
        name=PAI_PRIMARY_CHANNEL,
        title="PAI Counselor",
        created_by=PAI_AGENT_NAME,
        master_agent=PAI_AGENT_NAME,
        status="active",
    )
    db.add(channel)
    db.flush()
    db.add(ChannelMember(channel_id=channel.id, agent_name=PAI_AGENT_NAME))

    # Persist the greeting directly (same shape as a pipeline-posted PAI Counselor chat
    # message). No SSE publish needed: the workspace was created milliseconds
    # ago, nobody is subscribed yet — the first page load reads it from the DB.
    db.add(EventRecord(
        id=str(uuid.uuid4()),
        network_id=workspace.id,
        type="workspace.message.posted",
        source=f"openagents:{PAI_AGENT_NAME}",
        target=f"channel/{channel.name}",
        payload={"content": WELCOME_GREETING, "message_type": "chat"},
        metadata_={
            "target_agents": ["__no_response__"],
            "suggestions": WELCOME_SUGGESTIONS,
        },
        timestamp=int(time.time() * 1000),
        visibility="channel",
    ))
    db.flush()
    logger.info("pai: seeded welcome thread in workspace %s", workspace.id)
    return True


# Compatibility name for existing workspace-creation call sites.
seed_welcome_thread = ensure_primary_conversation


# ---------------------------------------------------------------------------
# In-process API client
# ---------------------------------------------------------------------------

class WorkspaceApi:
    """Calls the workspace's own HTTP API in-process (ASGI transport).

    Every PAI Counselor tool goes through the real FastAPI app — routing, auth
    (X-Workspace-Token), validation, serialization, and side effects like SSE
    publishes — instead of querying the database directly. No network hop.
    """

    def __init__(self, workspace_id: str, token: str):
        self.workspace_id = workspace_id
        self.token = token

    async def request(
        self, method: str, path: str, *,
        json: Optional[dict] = None, params: Optional[dict] = None,
    ) -> dict:
        """Perform a request; return {"ok": True, "data": ...} or
        {"ok": False, "error": ...}. Never raises."""
        # Imported lazily: app.main transitively imports this module.
        from app.main import app

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://pai.internal",
                timeout=30,
            ) as client:
                resp = await client.request(
                    method, path,
                    json=json, params=params,
                    headers={"X-Workspace-Token": self.token},
                )
        except Exception as exc:
            logger.exception("pai api: %s %s failed", method, path)
            return {"ok": False, "error": f"internal request failed: {exc}"[:200]}

        try:
            body = resp.json()
        except Exception:
            body = {}
        if resp.status_code == 200:
            return {"ok": True, "data": body.get("data")}
        return {
            "ok": False,
            "error": body.get("message") or f"HTTP {resp.status_code}",
            "status": resp.status_code,
        }

    async def _raw_request(self, method: str, path: str, **kwargs):
        from app.main import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pai.internal", timeout=30) as client:
            return await client.request(method, path, headers={"X-Workspace-Token": self.token}, **kwargs)

    async def get(self, path: str, **params: Any) -> dict:
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, json: Optional[dict] = None) -> dict:
        return await self.request("POST", path, json=json)

    async def delete(self, path: str) -> dict:
        return await self.request("DELETE", path)

    async def get_text(self, path: str, max_chars: int = 50000) -> dict:
        try:
            response = await self._raw_request("GET", path)
            if response.status_code != 200:
                return {"ok": False, "error": f"HTTP {response.status_code}"}
            return {"ok": True, "content": response.text[:max(1, min(max_chars, 100000))], "content_type": response.headers.get("content-type")}
        except Exception as exc:
            logger.exception("pai api text request failed path=%s", path)
            return {"ok": False, "error": str(exc)[:200]}

    async def get_base64(self, path: str, max_bytes: int) -> dict:
        try:
            response = await self._raw_request("GET", path)
            if response.status_code != 200:
                return {"ok": False, "error": f"HTTP {response.status_code}"}
            if len(response.content) > max_bytes:
                return {"ok": False, "error": "Response is too large"}
            return {"ok": True, "content_base64": base64.b64encode(response.content).decode("ascii"), "content_type": response.headers.get("content-type")}
        except Exception as exc:
            logger.exception("pai api binary request failed path=%s", path)
            return {"ok": False, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PAI_SYSTEM_PROMPT = """\\
You are PAI Counselor, the primary education counselor inside Placement AI.

You are the student's main interface to their education journey.

Your role is to understand what the student wants, ask useful questions when \\
necessary, explain education options clearly, and help the student decide what \\
to do next.

You operate inside a multi-agent workspace. Other specialist Placement AI agents \\
may be installed later. When specialist agents are available, you should be able \\
to discover them and collaborate with them through the workspace.

You are not a coding assistant.
You are not a Placement AI onboarding assistant.
You are not a device-setup assistant.

Do not claim that an action has been completed unless the system confirms it.
"""


async def workspace_state_summary(api: WorkspaceApi) -> str:
    """A short, live snapshot (via the API) injected into the system prompt so
    PAI Counselor is grounded in what actually exists. Never raises — on API failure it
    returns a minimal note rather than blocking the reply."""
    lines = ["Current workspace state (live):"]

    discover = await api.get("/v1/discover", network=api.workspace_id)
    if discover["ok"] and discover["data"]:
        agents = discover["data"].get("agents") or []
        real = [
            f"{a.get('address', '').removeprefix('openagents:')} ({a.get('status')})"
            for a in agents if not a.get("builtin")
        ]
        if real:
            lines.append(f"- Connected agents (besides you): {', '.join(real)}")
        else:
            lines.append(
                "- No other agents are connected yet — the user has only you "
                "(PAI Counselor). Offer to explain the available agent options."
            )
        channels = discover["data"].get("channels") or []
        if channels:
            titles = [c.get("title") or c.get("address", "") for c in channels[:15]]
            lines.append(f"- Existing threads: {', '.join(t for t in titles if t)}")
        else:
            lines.append("- No threads created yet.")
    else:
        lines.append("- (agent/thread state unavailable right now)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools (OpenAI function-calling schemas + executor)
# ---------------------------------------------------------------------------

def build_tools() -> list[dict]:
    """Compatibility facade; schemas are owned by the shared ToolRegistry."""
    from app.tools import get_tool_registry
    return get_tool_registry().openai_tools_for_agent(PAI_ALLOWED_TOOLS)



async def execute_tool(
    api: WorkspaceApi, agent_name: str, name: str, args: dict,
) -> dict:
    """Compatibility facade; execution is owned by the shared ToolExecutor."""
    from app.tools import ToolContext, get_tool_executor
    aliases = {
        "list_agents": "workspace.agents.list", "list_threads": "workspace.threads.list",
        "create_thread": "workspace.thread.create", "list_tasks": "tasks.list",
        "create_task": "tasks.create",
    }
    context = ToolContext(
        workspace_id=api.workspace_id, agent_name=agent_name, api=api,
        allowed_tools=frozenset(PAI_ALLOWED_TOOLS),
    )
    return await get_tool_executor().execute(aliases.get(name, name), args, context)
