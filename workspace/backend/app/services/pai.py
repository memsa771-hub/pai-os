# -*- coding: utf-8 -*-
"""
PAI Counselor — Placement AI's built-in education counselor.

It is a fixed system service, auto-provisioned as a workspace participant for
event routing. Its model credentials are held once in server configuration;
there is no per-workspace agent or provider configuration.

Tool calls go through the REAL workspace HTTP API via an in-process ASGI
client (``WorkspaceApi``) — never direct DB queries — so auth, validation,
serialization, and side effects (SSE publish, background tasks) behave exactly
as they do for any other client.

The built-in participant type is the reserved ``"system:pai"`` value.
"""

import logging
import base64
from typing import Any, Optional

import httpx
from sqlalchemy import select

from app.config import config
from app.models import WorkspaceMember

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Identity constants — the single source of truth for "what is the built-in".
# ---------------------------------------------------------------------------

PAI_AGENT_NAME = "pai"
PAI_AGENT_TYPE = "system:pai"
PAI_PRIMARY_CHANNEL = "pai-counselor"
# PAI Counselor's tool boundary: lightweight reads/context-inspection plus the
# two Operator hand-off tools — nothing that performs real execution (writes,
# browser automation, destructive or multi-step actions) and nothing that lets
# the student manage agents or spin up threads (PAI has no agent picker; see
# PAI_SYSTEM_PROMPT below — that's Operator's business, not a conversational
# one). Those live behind PAI Operator (see app/services/operator.py), which
# discovers them itself via the "operator" tool audience (see
# app/tools/registry.py) rather than a list maintained here.
#
# This tuple must stay a subset of what app/tools/builtin/__init__.py tags
# audiences=..."counselor"... for every non-operator.* entry — see
# TestToolBoundary.test_allowed_tools_match_counselor_audience in
# tests/test_pai.py, which fails loudly if the two ever drift apart again
# (they already have once: workspace.agents.list/workspace.thread.create used
# to be listed here despite being real execution, not lightweight reads).
#
# Do not add write/execution tools to this tuple — route that work through
# operator.delegate instead; see the module docstring and PAI_SYSTEM_PROMPT
# below for the counsel-vs-execute split this enforces.
PAI_ALLOWED_TOOLS = (
    "workspace.threads.list", "tasks.list", "files.list", "files.read",
    # PAI Operator — see app/services/operator.py. Counselor never touches
    # execution tools directly; it delegates and reads status back through these.
    "operator.delegate", "operator.status", "operator.resume",
    # Counselor reads canonical context; Operator intake proposes structured deltas.
    "memory.context", "vault.get", "memory.search", "memory.episodes",
)


def is_builtin_agent_type(agent_type: Optional[str]) -> bool:
    """True if a WorkspaceMember.agent_type belongs to the built-in assistant."""
    return (agent_type or "") == PAI_AGENT_TYPE


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

    Creates the internal ``WorkspaceMember`` used by event routing if PAI
    Counselor isn't already present. Caller commits the change.

    NOTE: does not run when ``should_provision()`` is False.
    """
    if not should_provision():
        return False

    workspace_id = str(workspace.id)

    # Namespace lock BEFORE the reads, so a concurrent rename/join can't
    # invalidate what we read here before we write.
    from app.workspace import naming
    naming.lock_member_namespace(db, workspace.id)

    existing_member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == PAI_AGENT_NAME,
        )
    ).scalar_one_or_none()

    # A live PAI Counselor already exists — nothing to do.
    if existing_member and existing_member.status != "removed":
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
        actor: Optional[str] = None,
    ) -> dict:
        """Perform a request; return {"ok": True, "data": ...} or
        {"ok": False, "error": ...}. Never raises.

        `actor` is the calling tool's ToolContext principal (e.g.
        "openagents:pai-operator"). It travels in a header signed with a
        per-process secret rather than in the request body, because a body
        field is something any caller can write — see app/event_identity.py.
        """
        # Imported lazily: app.main transitively imports this module.
        from app.main import app

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://pai.internal",
                timeout=30,
            ) as client:
                headers = {"X-Workspace-Token": self.token}
                if actor:
                    from app.security.event_identity import INTERNAL_ACTOR_HEADER, mint_internal_actor
                    headers[INTERNAL_ACTOR_HEADER] = mint_internal_actor(actor)
                resp = await client.request(
                    method, path,
                    json=json, params=params,
                    headers=headers,
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

    async def get(self, path: str, *, actor: Optional[str] = None, **params: Any) -> dict:
        return await self.request("GET", path, params=params or None, actor=actor)

    async def post(self, path: str, json: Optional[dict] = None, *,
                   actor: Optional[str] = None) -> dict:
        return await self.request("POST", path, json=json, actor=actor)

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

from app.services.counselor_prompt import PAI_SYSTEM_PROMPT


async def workspace_state_summary(api: WorkspaceApi) -> str:
    """A short, live snapshot (via the API) injected into the system prompt so
    PAI Counselor is grounded in what actually exists. Never raises — on API failure it
    returns a minimal note rather than blocking the reply.

    That promise is enforced here rather than inherited. `WorkspaceApi.request`
    already converts transport errors into an ``ok: False`` dict, so the body
    below is safe *today* — but the guarantee callers rely on is this
    function's, not that one's, and an unexpected response shape (``data`` that
    is not a dict) would otherwise raise straight through
    ``counseling.runtime._run_turn``'s ``asyncio.gather``, which has no
    ``return_exceptions`` and would lose the student's whole turn over a
    grounding nicety. Grounding is an enhancement to a reply, never a
    precondition for one.
    """
    lines = ["Current workspace state (live):"]

    try:
        discover = await api.get("/v1/discover", network=api.workspace_id)
        data = discover.get("data") if discover.get("ok") else None
        if isinstance(data, dict):
            agents = data.get("agents") or []
            real = [
                f"{a.get('address', '').removeprefix('openagents:')} ({a.get('status')})"
                for a in agents if not a.get("builtin")
            ]
            if real:
                # Grounding only — e.g. a real background agent a developer has
                # running. Never surface this to the student as something to
                # manage; see the "no agent picker" rule in PAI_SYSTEM_PROMPT.
                lines.append(f"- Other internal processes active: {', '.join(real)}")
            else:
                lines.append("- You are the student's only point of contact right now.")
            channels = data.get("channels") or []
            if channels:
                titles = [c.get("title") or c.get("address", "") for c in channels[:15]]
                lines.append(f"- Existing threads: {', '.join(t for t in titles if t)}")
            else:
                lines.append("- No threads created yet.")
        else:
            lines.append("- (agent/thread state unavailable right now)")
    except Exception:
        logger.exception("pai: workspace state summary failed for %s", api.workspace_id)
        return "Current workspace state (live): (unavailable)"

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools (OpenAI function-calling schemas + executor)
# ---------------------------------------------------------------------------

def allowed_tools_for_mode(mode: str = "normal") -> frozenset[str]:
    # Legacy completion mode is a reporting metric, not the counseling gate.
    # Runtime removes execution tools until the Student Mirror is confirmed.
    return frozenset(PAI_ALLOWED_TOOLS)


def build_tools(mode: str = "normal") -> list[dict]:
    """Compatibility facade; schemas are owned by the shared ToolRegistry."""
    from app.tools import get_tool_registry
    from app.memory.permissions import COUNSELOR_CAPABILITIES
    return get_tool_registry().openai_tools_for_agent(
        allowed_tools_for_mode(mode), granted_capabilities=COUNSELOR_CAPABILITIES,
    )



async def execute_tool(
    api: WorkspaceApi, agent_name: str, name: str, args: dict,
) -> dict:
    """Compatibility facade; execution is owned by the shared ToolExecutor."""
    from app.tools import AUDIENCE_COUNSELOR, ToolContext, get_tool_executor
    from app.memory.permissions import capabilities_for_agent
    aliases = {
        "list_agents": "workspace.agents.list", "list_threads": "workspace.threads.list",
        "create_thread": "workspace.thread.create", "list_tasks": "tasks.list",
        "create_task": "tasks.create",
    }
    context = ToolContext(
        workspace_id=api.workspace_id, agent_name=agent_name, api=api,
        allowed_tools=frozenset(PAI_ALLOWED_TOOLS), audience=AUDIENCE_COUNSELOR,
        # Keyed on the calling agent, so this facade cannot be used to borrow
        # Counselor's grant from a different agent.
        granted_capabilities=capabilities_for_agent(agent_name),
    )
    return await get_tool_executor().execute(aliases.get(name, name), args, context)
