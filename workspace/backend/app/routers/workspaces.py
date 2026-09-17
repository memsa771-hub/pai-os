# -*- coding: utf-8 -*-
"""
Workspace management endpoints — CRUD for the workspace itself.

These are NOT part of the ONM spec — they manage the product layer
(creating networks, listing user's workspaces, updating settings).

POST   /v1/workspaces              Create a new workspace
GET    /v1/workspaces              List workspaces
GET    /v1/workspaces/{id}         Get workspace details
PATCH  /v1/workspaces/{id}         Update workspace settings
DELETE /v1/workspaces/{id}         Delete workspace
PATCH  /v1/workspaces/{id}/members/{name}  Update agent description/role
"""

import json as _json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import naming
from app.config import config
from app.database import get_db
from app.models import (
    Channel,
    ChannelMember,
    User,
    Workspace,
    WorkspaceMember,
)
from app.access import (
    is_workspace_owner,
    resolve_current_user,
    verify_workspace_access,
)
from app.response import ResponseCode, json_response, success_response
from app.routers.network import _workspace_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces", tags=["Workspaces"])

AGENT_TIMEOUT = timedelta(seconds=config.AGENT_TIMEOUT_SECONDS)


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    """Extract bearer token from Authorization header."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _verify_workspace_access(workspace, token: Optional[str], authorization: Optional[str]) -> bool:
    """Check if the caller has access to a workspace.

    Thin wrapper over the single source of truth in app.access — kept here so
    the many callers importing this name don't have to change. (This path now
    also accepts Sign in with Apple bearers, matching the network router; the
    old copy verified a single hosted identity provider only.)
    """
    from app.access import verify_workspace_access
    return verify_workspace_access(workspace, token, authorization)


def _workspace_access_denied(authorization: Optional[str]):
    """Distinguish missing/invalid auth (401) from a valid non-member (403)."""
    bearer = _extract_bearer(authorization)
    if bearer:
        from app.firebase_auth import verify_identity_token
        if verify_identity_token(bearer):
            return json_response(ResponseCode.FORBIDDEN, "Workspace membership required")
    return json_response(ResponseCode.UNAUTHORIZED, "Invalid workspace credentials")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class WorkspaceCreateRequest(BaseModel):
    name: str
    agent_name: Optional[str] = None   # Optional — if provided, becomes master member
    agent_type: Optional[str] = None   # "claude", "openclaw", etc.
    creator_email: Optional[str] = None

class ChannelUpdateRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    starred: Optional[bool] = None
    master_agent: Optional[str] = None  # Reassign channel master
    orchestration_mode: Optional[str] = None  # "dynamic" | "master" | "workflow"
    orchestration_instruction: Optional[str] = None  # legacy free-text plan
    workflow_id: Optional[str] = None  # structured workflow to drive this thread ("" clears)
    auto_title: bool = False  # When True, title update is from auto-titling (don't mark as manually set)

class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[dict] = None
    status: Optional[str] = None
    # Enforced-login toggle (v1.0). Only owner/admin (or a workspace-token
    # holder) may change it — see update_workspace.
    require_login: Optional[bool] = None
    # Convenience top-level toggle for the Browser Fabric viewer in clients.
    # Stored inside `settings.browser_enabled` so we don't need a schema
    # migration — but exposed as a typed field so clients don't have to
    # round-trip the whole settings dict to flip one bool.
    browser_enabled: Optional[bool] = None
    browserfabric_api_key: Optional[str] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_bf_key(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) > 12:
        return key[:8] + "..." + key[-4:]
    return key[:4] + "..."


def _format_workspace(ws: Workspace, members: list, now: datetime) -> dict:
    agents = []
    for m in members:
        status = m.status
        is_cloud = (m.agent_type or "").startswith("cloud:")
        if not is_cloud and m.last_heartbeat:
            # Ensure timezone-aware comparison (SQLite stores naive datetimes)
            heartbeat = m.last_heartbeat
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            if (now - heartbeat) > AGENT_TIMEOUT:
                status = "offline"
        agents.append({
            "agentName": m.agent_name,
            "displayName": m.display_name,
            "role": m.role,
            "agentType": m.agent_type,
            "status": status,
            "description": m.description,
            "workingDir": m.working_dir,
            "model": m.model,
            "builtin": (m.agent_type or "") == "cloud:placement_ai",
            "lastHeartbeatAt": m.last_heartbeat.isoformat() if m.last_heartbeat else None,
            "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
        })

    settings = ws.settings or {}
    return {
        "workspaceId": str(ws.id),
        "slug": ws.slug,
        "name": ws.name,
        "creatorEmail": ws.creator_email,
        "requireLogin": bool(ws.require_login),
        "settings": settings,
        # Surface browser_enabled at the top level for clients that don't
        # want to dig into the settings dict. Mirrors what's inside settings.
        "browserEnabled": bool(settings.get("browser_enabled", False)),
        "browserfabricApiKey": _mask_bf_key(settings.get("browserfabric_api_key")),
        "status": ws.status,
        "createdAt": ws.created_at.isoformat() if ws.created_at else None,
        "lastActivityAt": ws.last_activity_at.isoformat() if ws.last_activity_at else None,
        "agents": agents,
    }


def _format_channel(ch: Channel) -> dict:
    return {
        "channelId": str(ch.id),
        "workspaceId": str(ch.workspace_id),
        "name": ch.name,
        "title": ch.title,
        "titleManuallySet": bool(ch.title_manually_set),
        "createdBy": ch.created_by,
        "masterAgent": ch.master_agent,
        "orchestrationMode": ch.orchestration_mode or "dynamic",
        "orchestrationInstruction": ch.orchestration_instruction,
        "workflowId": ch.workflow_id,
        "resumeFrom": ch.resume_from,
        "status": ch.status,
        "starred": bool(ch.starred),
        "participants": [p.agent_name for p in (ch.participants or [])],
        "createdAt": ch.created_at.isoformat() if ch.created_at else None,
    }


# ---------------------------------------------------------------------------
# POST /v1/workspaces — Create workspace
# ---------------------------------------------------------------------------

@router.post("")
def create_workspace(
    body: WorkspaceCreateRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Create a new workspace (= ONM network).

    Placement AI v2.0: a verified human account owns at most one active
    workspace. When called with a verified identity bearer and that user
    already has one (`owner_user_id`), this returns their EXISTING canonical
    workspace instead of creating a second — idempotent-create semantics, so
    any lingering client call can't produce duplicates. Anonymous creation (no
    bearer) still works for backward/machine compatibility — creator_email
    falls back to the request body, and no owner_user_id is set (the caller
    isn't an authenticated student).
    """
    # The creating agent's name enters router prompts verbatim — same
    # character policy as the join handler.
    if body.agent_name:
        name_problem = naming.agent_name_problem(body.agent_name)
        if name_problem:
            return json_response(
                ResponseCode.BAD_REQUEST, f"Invalid agent name: {name_problem}",
            )

    now = datetime.now(timezone.utc)

    from app.access import get_or_create_owned_workspace, resolve_current_user
    owner = resolve_current_user(db, authorization)

    if owner is not None:
        # A verified human never gets a second workspace — regardless of what
        # else the request asked for. (`agent_name` is ignored here; seeding
        # an agent into an existing workspace is a separate operation.)
        workspace = get_or_create_owned_workspace(db, owner)
        db.commit()
        db.refresh(workspace)
        return success_response({
            "workspaceId": str(workspace.id),
            "slug": workspace.slug,
            "name": workspace.name,
            "token": workspace.password_hash,
            "channel": None,
        })

    # Generate slug and token
    slug = secrets.token_hex(4)
    token = secrets.token_urlsafe(32)

    creator_email = owner.email if owner else body.creator_email

    workspace = Workspace(
        slug=slug,
        name=body.name,
        creator_email=creator_email,
        owner_user_id=owner.id if owner else None,
        password_hash=token,
        # Every new workspace enforces login by default (secure-by-default).
        # Machine access via the workspace token is unaffected — agents, the
        # agn CLI and ?token= links all pass the token rule — so anonymous
        # (CLI) creation still works end-to-end. An owner/admin can opt out
        # via PATCH require_login=false (Security settings).
        require_login=True,
        settings={},
        status="active",
    )
    db.add(workspace)
    db.flush()

    # Optionally add the creating agent as master member
    if body.agent_name:
        member = WorkspaceMember(
            workspace_id=workspace.id,
            agent_name=body.agent_name,
            role="master",
            agent_type=body.agent_type,
            status="online",
            last_heartbeat=now,
        )
        db.add(member)

    # Seed a default "Session 1" channel ONLY when we know which agent to put in
    # it. An empty channel (no participants) would surface as a thread with no
    # agent — instead, an agent-less workspace starts with zero threads and the
    # user creates their first session via the New Thread dialog (which selects
    # agents). When agent_name is provided (e.g. tests, TUI), the starter
    # channel is created with that agent as master + participant.
    channel = None
    if body.agent_name:
        channel = Channel(
            workspace_id=workspace.id,
            name=f"session-{secrets.token_hex(4)}",
            title="Session 1",
            created_by=body.agent_name,
            master_agent=body.agent_name,
            status="active",
        )
        db.add(channel)
        db.flush()
        db.add(ChannelMember(
            channel_id=channel.id,
            agent_name=body.agent_name,
        ))

    # Auto-provision the built-in PAI Counselor onboarding assistant (no-op when disabled
    # or no server key is configured). Never let this block workspace creation.
    try:
        from app.services.pai import provision_pai, seed_welcome_thread
        if provision_pai(db, workspace):
            # Every workspace gets the same canonical PAI conversation, even
            # when a legacy caller also requested a starter agent/session.
            seed_welcome_thread(db, workspace)
    except Exception:
        logger.warning("create_workspace: failed to provision PAI Counselor", exc_info=True)

    db.commit()
    db.refresh(workspace)

    return success_response({
        "workspaceId": str(workspace.id),
        "slug": workspace.slug,
        "name": workspace.name,
        "token": token,
        "channel": _format_channel(channel) if channel else None,
    })


# ---------------------------------------------------------------------------
# GET /v1/workspaces — List workspaces
# ---------------------------------------------------------------------------

@router.get("")
def list_workspaces(
    creator_email: Optional[str] = Query(None),
    agent_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List workspaces, optionally filtered by creator or agent membership."""
    query = select(Workspace).where(Workspace.status != "deleted")

    if creator_email:
        query = query.where(Workspace.creator_email == creator_email)

    if agent_name:
        query = query.join(WorkspaceMember).where(
            WorkspaceMember.agent_name == agent_name
        )

    query = query.options(selectinload(Workspace.members))
    workspaces = db.execute(query.order_by(Workspace.last_activity_at.desc())).scalars().all()
    now = datetime.now(timezone.utc)

    results = [_format_workspace(ws, ws.members, now) for ws in workspaces]

    return success_response(results)


# ---------------------------------------------------------------------------
# GET /v1/workspaces/skill-catalog  (static — no auth required)
# Must be defined before /{workspace_id} to avoid path capture.
# ---------------------------------------------------------------------------

@router.get("/skill-catalog")
async def skill_catalog():
    """Return the full skill catalog (public, static data)."""
    from app.skill_catalog import get_catalog
    return success_response(get_catalog())


# ---------------------------------------------------------------------------
# GET /v1/workspaces/{workspace_id} — Get workspace
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Get workspace details by ID or slug."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace or workspace.status == "deleted":
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    members = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    ).scalars().all()

    now = datetime.now(timezone.utc)
    return success_response(_format_workspace(workspace, members, now))


# ---------------------------------------------------------------------------
# PATCH /v1/workspaces/{workspace_id} — Update workspace
# ---------------------------------------------------------------------------

@router.patch("/{workspace_id}")
def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Update workspace name, settings, or status."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    if body.name is not None:
        workspace.name = body.name
    if body.settings is not None:
        workspace.settings = body.settings
    if body.browser_enabled is not None:
        current = dict(workspace.settings or {})
        current["browser_enabled"] = body.browser_enabled
        workspace.settings = current
    if body.browserfabric_api_key is not None:
        current = dict(workspace.settings or {})
        if body.browserfabric_api_key == "":
            current.pop("browserfabric_api_key", None)
        else:
            current["browserfabric_api_key"] = body.browserfabric_api_key
        workspace.settings = current
    if body.status is not None:
        workspace.status = body.status

    if body.require_login is not None:
        # Enforced-login is an owner/admin control (a workspace-token holder is
        # trusted and also permitted). Other members can't flip it.
        from app.access import verify_workspace_access
        if not verify_workspace_access(workspace, x_workspace_token, authorization, db=db):
            return json_response(ResponseCode.FORBIDDEN, "Only an owner or admin can change login enforcement")
        workspace.require_login = body.require_login

    db.commit()
    db.refresh(workspace)

    members = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    ).scalars().all()

    now = datetime.now(timezone.utc)
    return success_response(_format_workspace(workspace, members, now))


# ---------------------------------------------------------------------------
# POST /v1/workspaces/{workspace_id}/claim — Claim workspace ownership
# ---------------------------------------------------------------------------

@router.post("/{workspace_id}/claim")
def claim_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """
    Claim ownership of an unowned workspace.

    Requires a valid identity bearer. Sets `owner_user_id`, which IS the access
    grant — `creator_email` is kept for display only and no longer authorizes
    anything (see app/access.py). A user already owning an active workspace
    cannot claim a second one: that is the product invariant, and
    `uq_workspace_owner_active` enforces it in the database regardless.
    """
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid or expired token")
    email = user.email

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        db.commit()  # persist the lazily created User row
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    already_owned_by_other = (
        workspace.owner_user_id is not None
        and str(workspace.owner_user_id) != str(user.id)
    )
    if already_owned_by_other:
        db.commit()
        return json_response(ResponseCode.FORBIDDEN, "Workspace already claimed by another user")

    if str(workspace.owner_user_id or "") != str(user.id):
        existing = db.execute(
            select(Workspace).where(
                Workspace.owner_user_id == user.id,
                Workspace.status == "active",
                Workspace.id != workspace.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.commit()
            return json_response(
                ResponseCode.FORBIDDEN,
                "You already have a personal workspace",
            )

    workspace.owner_user_id = user.id
    workspace.creator_email = email
    db.commit()
    db.refresh(workspace)

    members = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    ).scalars().all()

    now = datetime.now(timezone.utc)
    return success_response(_format_workspace(workspace, members, now))


# ---------------------------------------------------------------------------
# POST /v1/workspaces/{workspace_id}/rotate-token
# ---------------------------------------------------------------------------

@router.post("/{workspace_id}/rotate-token")
def rotate_token(
    workspace_id: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Rotate the workspace token. Old token immediately stops working.

    Requires either the current workspace token or verified human bearer auth
    from the workspace owner.
    """
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    new_token = secrets.token_urlsafe(32)
    workspace.password_hash = new_token
    db.commit()

    return success_response({
        "workspace_id": str(workspace.id),
        "token": new_token,
    })


# ---------------------------------------------------------------------------
# DELETE /v1/workspaces/{workspace_id}/members/{agent_name}
# ---------------------------------------------------------------------------

@router.delete("/{workspace_id}/members/{agent_name}")
def remove_member(
    workspace_id: str,
    agent_name: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Remove an agent from a workspace."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    from app.services.pai import PAI_AGENT_NAME
    if agent_name.casefold() == PAI_AGENT_NAME:
        return json_response(
            ResponseCode.FORBIDDEN,
            "PAI Counselor is a system agent and cannot be removed.",
        )

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    db.delete(member)
    db.commit()

    return success_response({"agent_name": agent_name, "removed": True})


# ---------------------------------------------------------------------------
# PATCH /v1/workspaces/{workspace_id}/members/{agent_name}
# ---------------------------------------------------------------------------

class MemberUpdateRequest(BaseModel):
    description: Optional[str] = None
    role: Optional[str] = None
    enabled_skills: Optional[Dict[str, bool]] = None
    # Display label, any script. Empty string clears it (falls back to agent_name).
    display_name: Optional[str] = None
    # Model id the agent should use (see /v1/cloud-agents/providers for the
    # available providers/models, but any id is accepted — that list is only
    # a curated suggestion). Empty string clears the override back to the
    # agent's own default.
    model: Optional[str] = None


@router.patch("/{workspace_id}/members/{agent_name}")
def update_member(
    workspace_id: str,
    agent_name: str,
    body: MemberUpdateRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Update an agent's metadata (description, role)."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    from app.services.pai import PAI_AGENT_NAME
    if agent_name.casefold() == PAI_AGENT_NAME:
        return json_response(
            ResponseCode.FORBIDDEN,
            "PAI Counselor is a system agent and cannot be modified.",
        )

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    if body.display_name is not None:
        display_name = body.display_name.strip()
        if not display_name:
            member.display_name = None
        else:
            if len(display_name) > naming.MAX_DISPLAY_NAME_LENGTH:
                return json_response(
                    ResponseCode.BAD_REQUEST,
                    f"Display name must be at most {naming.MAX_DISPLAY_NAME_LENGTH} characters",
                )
            # Control chars, Unicode line separators and bidi overrides could
            # forge extra lines in the router prompt — reject them.
            if naming.has_unsafe_chars(display_name):
                return json_response(
                    ResponseCode.BAD_REQUEST,
                    "Display name must not contain control or line-separator characters",
                )
            # Display names are routable aliases, sharing one namespace with
            # agent names. Lock the workspace row so a concurrent rename/join
            # can't pass this check simultaneously and commit a duplicate.
            naming.lock_member_namespace(db, workspace.id)
            clash = naming.find_alias_clash(
                db, workspace.id, display_name, exclude_agent=agent_name,
            )
            if clash:
                return json_response(
                    ResponseCode.BAD_REQUEST,
                    f"Display name conflicts with another member ('{clash}')",
                )
            member.display_name = display_name
    if body.description is not None:
        member.description = body.description
    if body.role is not None:
        member.role = body.role
    if body.enabled_skills is not None:
        from app.skill_catalog import get_skill_defaults
        defaults = get_skill_defaults()
        valid = {k: v for k, v in body.enabled_skills.items() if k in defaults}
        member.enabled_skills = valid or None
    if body.model is not None:
        model = body.model.strip()
        if len(model) > 200:
            return json_response(ResponseCode.BAD_REQUEST, "Model id too long")
        if model and naming.has_unsafe_chars(model):
            return json_response(
                ResponseCode.BAD_REQUEST,
                "Model id must not contain control characters",
            )
        member.model = model or None
        if (member.agent_type or "").startswith("cloud:"):
            # Cloud agents execute server-side; their runtime reads
            # cloud_agent_configs.model, so keep it in lockstep.
            if member.model:
                cloud_cfg = db.execute(
                    select(CloudAgentConfig).where(
                        CloudAgentConfig.workspace_id == workspace.id,
                        CloudAgentConfig.agent_name == agent_name,
                    )
                ).scalar_one_or_none()
                if cloud_cfg:
                    cloud_cfg.model = member.model
        else:
            # Tell a live adapter about the change; it respawns its CLI with
            # the new model on the next message (launchers without model
            # support ignore unknown control actions).
            _emit_agent_control_event(
                db, workspace, agent_name, "model.set", {"model": member.model},
            )

    db.commit()

    return success_response({
        "agentName": member.agent_name,
        "displayName": member.display_name,
        "description": member.description,
        "role": member.role,
        "enabledSkills": member.enabled_skills,
        "model": member.model,
    })


# ---------------------------------------------------------------------------
# POST /v1/workspaces/{workspace_id}/members/{agent_name}/generate-description
# ---------------------------------------------------------------------------

_DESCRIPTION_PROMPT = """\
Write a ONE-LINE role description for an AI agent in a multi-agent workspace, \
so a router can decide when to delegate tasks to it.

Agent name: {name}
Type: {agent_type}
Working directory: {working_dir}
Installed skills: {skills}

Recent messages this agent has posted (newest last):
{history}

Write ONE concise sentence (max ~16 words), third person, describing this \
agent's specialty/role and the kinds of tasks it handles. No name prefix, no \
quotes, no trailing period needed. If signal is thin, infer from the name, \
type, working directory, and skills. Output ONLY the sentence."""


@router.post("/{workspace_id}/members/{agent_name}/generate-description")
def generate_member_description(
    workspace_id: str,
    agent_name: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Draft a one-line role description for an agent using the router LLM.

    Uses the agent's recent activity + metadata (type, working dir, skills) to
    infer a specialty. Returns the suggestion WITHOUT saving — the client shows
    it for review and persists via PATCH /members/{name} if the user accepts.
    """
    from app.models import EventRecord
    from app.mods.workspace_mod import (
        _get_llm_client, _get_router_model, _get_router_api_key,
    )

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()
    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    if not _get_router_api_key():
        return json_response(
            ResponseCode.BAD_REQUEST,
            "Description generation is unavailable (no router LLM key configured)",
        )

    # Gather up to 15 of the agent's own recent chat messages for signal.
    recent = db.execute(
        select(EventRecord)
        .where(
            EventRecord.network_id == workspace.id,
            EventRecord.source == f"openagents:{agent_name}",
            EventRecord.type == "workspace.message.posted",
        )
        .order_by(EventRecord.timestamp.desc())
        .limit(15)
    ).scalars().all()
    recent.reverse()
    snippets = []
    for evt in recent:
        payload = evt.payload or {}
        if payload.get("message_type", "chat") != "chat":
            continue
        text = (payload.get("content") or "").strip().replace("\n", " ")
        if text:
            snippets.append(f"- {text[:200]}")
    history = "\n".join(snippets[-15:]) if snippets else "(no recent messages)"

    installed = []
    if isinstance(member.enabled_skills, dict):
        installed = member.enabled_skills.get("installed") or []
    skills = ", ".join(installed) if installed else "(none listed)"

    prompt = _DESCRIPTION_PROMPT.format(
        name=agent_name,
        agent_type=member.agent_type or "unknown",
        working_dir=member.working_dir or "(unknown)",
        skills=skills,
        history=history,
    )

    try:
        client, provider = _get_llm_client()
        model = _get_router_model()
        if provider == "openai":
            resp = client.chat.completions.create(
                model=model, max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
        else:
            resp = client.messages.create(
                model=model, max_tokens=60,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
    except Exception as e:
        logger.error("generate-description failed for %s: %s", agent_name, e)
        return json_response(ResponseCode.INTERNAL_ERROR, "Failed to generate description")

    # Strip stray wrapping quotes / trailing period the model sometimes adds.
    text = text.strip().strip('"').strip("'").rstrip(".").strip()
    return success_response({"agentName": agent_name, "description": text})


# ---------------------------------------------------------------------------
# POST /v1/workspaces/{workspace_id}/members/{agent_name}/skills/install
# DELETE /v1/workspaces/{workspace_id}/members/{agent_name}/skills/uninstall
# ---------------------------------------------------------------------------

class SkillInstallRequest(BaseModel):
    skill_id: str


class SkillStatusRequest(BaseModel):
    skill_id: str
    state: str  # "installing" | "installed" | "failed" | "uninstalled"
    path: Optional[str] = None
    error: Optional[str] = None
    partial: Optional[bool] = None  # SKILL.md fetched but bundled files missing


_VALID_SKILL_STATES = {"installing", "installed", "failed", "uninstalled"}


class CustomSkillRegisterRequest(BaseModel):
    """Register a previously-uploaded workspace file as a custom skill.

    The file must already exist as a ``FileRecord`` in *this* workspace (upload
    via ``POST /v1/files`` first, then register with the returned ``file_id``).
    """
    file_id: str
    id: Optional[str] = None            # auto-derived from filename when omitted
    name: Optional[str] = None
    description: Optional[str] = None
    filename: Optional[str] = None      # original name, for display metadata only


def _custom_skills_map(workspace) -> dict:
    """Return a shallow copy of ``settings["custom_skills"]`` (id → metadata)."""
    return dict((workspace.settings or {}).get("custom_skills") or {})


def _emit_agent_control_event(db, workspace, agent_name: str, action: str, payload: dict) -> None:
    """Persist a ``workspace.agent.control`` event targeted at one agent and
    publish it to the workspace's Redis pub/sub channel.

    The launcher's per-agent control poller
    (``GET /v1/events?type=workspace.agent.control&target=openagents:<name>``)
    picks this up and dispatches the action to the adapter. We write the
    EventRecord directly — mirroring mod/persistence — rather than running the
    full event pipeline, because the caller has already verified workspace
    access and there is no human/agent source to authenticate.
    """
    from app import cache
    from app.models import EventRecord

    event_id = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    full_payload = {"action": action, **(payload or {})}
    record = EventRecord(
        id=event_id,
        network_id=workspace.id,
        type="workspace.agent.control",
        source="human:system",
        target=f"openagents:{agent_name}",
        payload=full_payload,
        metadata_={},
        timestamp=timestamp,
        visibility="direct",
    )
    db.add(record)
    db.flush()

    try:
        snapshot = {
            "id": event_id,
            "type": "workspace.agent.control",
            "source": "human:system",
            "target": f"openagents:{agent_name}",
            "payload": full_payload,
            "metadata": {},
            "timestamp": timestamp,
        }
        cache.publish_event(
            f"ws:{workspace.id}:events",
            _json.dumps(snapshot, default=str, separators=(",", ":")).encode(),
        )
    except Exception:
        # Pub/sub is a fast-path optimization; the poller still finds the
        # persisted event. Never fail the request on a cache hiccup.
        logger.warning("install_skill: failed to publish control event to cache", exc_info=True)


def _set_skill_status(skills_data: dict, skill_id: str, state: str,
                      path: Optional[str] = None, error: Optional[str] = None,
                      partial: Optional[bool] = None) -> dict:
    """Update the per-skill status map inside an ``enabled_skills`` dict.

    Keeps the legacy ``installed`` list in sync (only successfully-installed
    skills appear there, so existing readers keep working) and stores richer
    state under ``skill_status`` for the UI.
    """
    status_map = dict(skills_data.get("skill_status", {}))
    entry = {"state": state, "updated_at": int(time.time() * 1000)}
    if path:
        entry["path"] = path
    if error:
        entry["error"] = error[:2000]
    if partial:
        entry["partial"] = True
    status_map[skill_id] = entry
    skills_data["skill_status"] = status_map

    installed = [s for s in skills_data.get("installed", []) if s != skill_id]
    if state == "installed":
        installed.append(skill_id)
    skills_data["installed"] = installed
    return skills_data


@router.post("/{workspace_id}/members/{agent_name}/skills/install")
async def install_skill(
    workspace_id: str,
    agent_name: str,
    body: SkillInstallRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Request installation of a third-party skill for an agent.

    Marks the skill ``installing`` and emits a ``skill.install`` control event
    so the launcher actually installs it into the agent's skills directory.
    The agent reports back via ``/skills/status`` to flip the state to
    ``installed`` or ``failed`` — the skill is NOT marked installed here.
    """
    from app.skill_catalog import find_skill

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()
    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    # Built-in catalog first; then fall back to this workspace's custom skills.
    skill = find_skill(body.skill_id)
    custom = None if skill else _custom_skills_map(workspace).get(body.skill_id)
    if not skill and not custom:
        return json_response(ResponseCode.NOT_FOUND, f"Unknown skill: {body.skill_id}")

    # For a custom skill, confirm its backing upload still exists and belongs to
    # this workspace BEFORE marking installing / emitting the control event.
    # Otherwise a file deleted from Workspace Files would only surface as a
    # late agent-side readFile failure; fail fast here with a clear, actionable
    # message so the UI can tell the user to re-upload.
    if custom:
        from app.models import FileRecord
        file_id = custom.get("file_id")
        file_rec = db.execute(
            select(FileRecord).where(FileRecord.id == file_id)
        ).scalar_one_or_none() if file_id else None
        if (not file_rec or file_rec.status != "active"
                or str(file_rec.workspace_id) != str(workspace.id)):
            return json_response(
                ResponseCode.CONFLICT,
                "This skill's uploaded file was deleted. Please re-upload the skill.",
            )

    skills_data = dict(member.enabled_skills or {})
    skills_data = _set_skill_status(skills_data, body.skill_id, "installing")
    member.enabled_skills = skills_data

    if custom:
        # Custom skill: the agent downloads the uploaded file from workspace
        # storage via WorkspaceClient.readFile. Send only metadata + file_id —
        # NEVER the file contents / base64 in the control event payload.
        _emit_agent_control_event(db, workspace, agent_name, "skill.install", {
            "skill": {
                "id": custom["id"],
                "name": custom.get("name", custom["id"]),
                "description": custom.get("description", ""),
                "source_type": custom.get("source_type", "workspace_file"),
                "file_id": custom.get("file_id"),
                "filename": custom.get("filename"),
                "content_type": custom.get("content_type"),
                "package_type": custom.get("package_type"),
            },
        })
    else:
        # Carry the catalog metadata the launcher needs to fetch the skill.
        _emit_agent_control_event(db, workspace, agent_name, "skill.install", {
            "skill": {
                "id": skill["id"],
                "name": skill.get("name", skill["id"]),
                "description": skill.get("description", ""),
                "source_repo": skill.get("source_repo", ""),
                "source_path": skill.get("source_path", ""),
            },
        })
    db.commit()

    logger.info(
        "install_skill: queued install of '%s' for agent '%s' in workspace %s",
        body.skill_id, agent_name, workspace.id,
    )
    return success_response({
        "agentName": agent_name,
        "skillId": body.skill_id,
        "action": "installing",
        "state": "installing",
        "installedSkills": list(skills_data.get("installed", [])),
        "skillStatus": skills_data.get("skill_status", {}),
    })


@router.post("/{workspace_id}/members/{agent_name}/skills/status")
async def report_skill_status(
    workspace_id: str,
    agent_name: str,
    body: SkillStatusRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Launcher → workspace callback reporting skill install progress/result.

    Updates ``enabled_skills.skill_status`` so the Skill Hub UI can render
    installing → installed / failed, and keeps the legacy ``installed`` list
    in sync. Also re-publishes to the SSE channel for instant UI updates.
    """
    if body.state not in _VALID_SKILL_STATES:
        return json_response(ResponseCode.BAD_REQUEST, f"Invalid state: {body.state}")

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()
    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    skills_data = dict(member.enabled_skills or {})
    if body.state == "uninstalled":
        # Drop the status entry entirely on uninstall.
        status_map = dict(skills_data.get("skill_status", {}))
        status_map.pop(body.skill_id, None)
        skills_data["skill_status"] = status_map
        skills_data["installed"] = [
            s for s in skills_data.get("installed", []) if s != body.skill_id
        ]
    else:
        skills_data = _set_skill_status(
            skills_data, body.skill_id, body.state, body.path, body.error, body.partial
        )
    member.enabled_skills = skills_data
    db.commit()

    if body.state == "failed":
        logger.error(
            "skill install FAILED: skill='%s' agent='%s' workspace=%s error=%s",
            body.skill_id, agent_name, workspace.id, body.error,
        )
    elif body.state == "installed" and body.partial:
        logger.warning(
            "skill installed PARTIALLY (SKILL.md only, bundled files missing): "
            "skill='%s' agent='%s'", body.skill_id, agent_name,
        )
    else:
        logger.info(
            "skill status: skill='%s' agent='%s' state='%s'",
            body.skill_id, agent_name, body.state,
        )

    # Push a lightweight status event so SSE-connected UIs update instantly.
    try:
        from app import cache
        snapshot = {
            "id": str(uuid.uuid4()),
            "type": "workspace.skill.status",
            "source": f"openagents:{agent_name}",
            "target": f"openagents:{agent_name}",
            "payload": {
                "skill_id": body.skill_id,
                "state": body.state,
                "error": body.error,
            },
            "metadata": {},
            "timestamp": int(time.time() * 1000),
        }
        cache.publish_event(
            f"ws:{workspace.id}:events",
            _json.dumps(snapshot, default=str, separators=(",", ":")).encode(),
        )
    except Exception:
        pass

    return success_response({
        "agentName": agent_name,
        "skillId": body.skill_id,
        "state": body.state,
        "installedSkills": list(skills_data.get("installed", [])),
        "skillStatus": skills_data.get("skill_status", {}),
    })


@router.post("/{workspace_id}/members/{agent_name}/skills/uninstall")
async def uninstall_skill(
    workspace_id: str,
    agent_name: str,
    body: SkillInstallRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Uninstall a third-party skill from an agent.

    Removes it from the DB immediately (optimistic) and emits a
    ``skill.uninstall`` control event so the launcher deletes the on-disk
    skill directory.
    """
    from app.skill_catalog import find_skill

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()
    if not member:
        return json_response(ResponseCode.NOT_FOUND, "Member not found")

    skills_data = dict(member.enabled_skills or {})
    installed = [s for s in skills_data.get("installed", []) if s != body.skill_id]
    skills_data["installed"] = installed
    status_map = dict(skills_data.get("skill_status", {}))
    status_map.pop(body.skill_id, None)
    skills_data["skill_status"] = status_map
    member.enabled_skills = skills_data

    skill = find_skill(body.skill_id) or {"id": body.skill_id}
    _emit_agent_control_event(db, workspace, agent_name, "skill.uninstall", {
        "skill": {
            "id": skill["id"],
            "name": skill.get("name", skill["id"]),
            "source_repo": skill.get("source_repo", ""),
            "source_path": skill.get("source_path", ""),
        },
    })
    db.commit()

    return success_response({
        "agentName": agent_name,
        "skillId": body.skill_id,
        "action": "uninstalled",
        "installedSkills": installed,
    })


# ---------------------------------------------------------------------------
# GET  /v1/workspaces/{workspace_id}/skills/custom   — list custom skills
# POST /v1/workspaces/{workspace_id}/skills/custom   — register a custom skill
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/skills/custom")
async def list_custom_skills(
    workspace_id: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """List this workspace's user-uploaded custom skills."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    return success_response({"skills": list(_custom_skills_map(workspace).values())})


@router.post("/{workspace_id}/skills/custom")
async def register_custom_skill(
    workspace_id: str,
    body: CustomSkillRegisterRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Register an already-uploaded workspace file as a custom skill.

    Validates that the file belongs to this workspace and that its bytes are a
    valid ``.md`` or ``.zip`` skill package, then stores metadata under
    ``Workspace.settings["custom_skills"]``. The file bytes are never copied
    into settings — only a ``file_id`` reference is kept.
    """
    from app.custom_skills import (
        CUSTOM_SKILL_CATEGORY,
        CUSTOM_SKILL_SOURCE_TYPE,
        CustomSkillError,
        derive_skill_id,
        inspect_package,
        is_valid_skill_id,
    )
    from app.models import FileRecord
    from app.skill_catalog import find_skill
    from app.storage import get_file_store

    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    # The file must exist AND belong to this workspace. The generic file
    # download route only checks "can you access the file's own workspace", so
    # this ownership check is genuinely required here — it stops a caller from
    # registering another workspace's file id into this workspace.
    file_rec = db.execute(
        select(FileRecord).where(FileRecord.id == body.file_id)
    ).scalar_one_or_none()
    if not file_rec or file_rec.status != "active":
        return json_response(ResponseCode.NOT_FOUND, "File not found")
    if str(file_rec.workspace_id) != str(workspace.id):
        return json_response(ResponseCode.NOT_FOUND, "File not found in this workspace")

    skill_id = (body.id or "").strip() or derive_skill_id(body.filename or file_rec.filename)
    if not is_valid_skill_id(skill_id):
        return json_response(
            ResponseCode.BAD_REQUEST,
            "Invalid skill id. Use letters, digits, '.', '_' or '-' and do not start with a dash.",
        )

    # No shadowing of a built-in catalog skill, and no duplicate custom id.
    if find_skill(skill_id):
        return json_response(
            ResponseCode.CONFLICT, f"'{skill_id}' conflicts with a built-in catalog skill",
        )
    existing = _custom_skills_map(workspace)
    if skill_id in existing:
        return json_response(
            ResponseCode.CONFLICT, f"A custom skill '{skill_id}' already exists in this workspace",
        )

    store = get_file_store()
    try:
        data = store.read(file_rec.storage_key)
    except Exception:
        return json_response(ResponseCode.BAD_REQUEST, "Could not read the uploaded file")

    # Validate by inspecting bytes — do NOT trust the stored content_type.
    try:
        pkg = inspect_package(data, file_rec.filename)
    except CustomSkillError as exc:
        return json_response(ResponseCode.BAD_REQUEST, str(exc))

    entry = {
        "id": skill_id,
        "name": (body.name or "").strip() or skill_id,
        "description": (body.description or "").strip(),
        "category": CUSTOM_SKILL_CATEGORY,
        "tags": [],
        "author": "Workspace user",
        "source_type": CUSTOM_SKILL_SOURCE_TYPE,
        "file_id": file_rec.id,
        "filename": os.path.basename(body.filename or file_rec.filename),
        "content_type": pkg["content_type"],
        "package_type": pkg["package_type"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist via copy-then-reassign: SQLAlchemy does not detect in-place edits
    # of a JSONB column (no MutableDict here), so we rebuild and reassign the
    # whole settings dict. The copy also preserves any other custom skills.
    current = dict(workspace.settings or {})
    skills = dict(current.get("custom_skills") or {})
    skills[skill_id] = entry
    current["custom_skills"] = skills
    workspace.settings = current
    db.add(workspace)
    db.commit()

    logger.info(
        "register_custom_skill: '%s' (%s) registered in workspace %s",
        skill_id, entry["package_type"], workspace.id,
    )
    return success_response(entry)


# ---------------------------------------------------------------------------
# GET /v1/workspaces/{workspace_id}/channels/{channel_name}
# ---------------------------------------------------------------------------

@router.get("/{workspace_id}/channels/{channel_name}")
def get_channel(
    workspace_id: str,
    channel_name: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Get channel details."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    from app.services.pai import PAI_PRIMARY_CHANNEL
    if channel_name == PAI_PRIMARY_CHANNEL:
        return json_response(
            ResponseCode.FORBIDDEN,
            "The PAI Counselor conversation is a system conversation and cannot be modified.",
        )

    channel = db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace.id,
            Channel.name == channel_name,
        )
    ).scalar_one_or_none()
    if not channel:
        return json_response(ResponseCode.NOT_FOUND, "Channel not found")

    return success_response(_format_channel(channel))


# ---------------------------------------------------------------------------
# PATCH /v1/workspaces/{workspace_id}/channels/{channel_name}
# ---------------------------------------------------------------------------

@router.patch("/{workspace_id}/channels/{channel_name}")
def update_channel(
    workspace_id: str,
    channel_name: str,
    body: ChannelUpdateRequest,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Update channel title or status."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    from app.services.pai import PAI_PRIMARY_CHANNEL
    if channel_name == PAI_PRIMARY_CHANNEL:
        return json_response(
            ResponseCode.FORBIDDEN,
            "The PAI Counselor conversation is managed by Placement AI",
        )

    channel = db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace.id,
            Channel.name == channel_name,
        )
    ).scalar_one_or_none()
    if not channel:
        return json_response(ResponseCode.NOT_FOUND, "Channel not found")

    if body.title is not None:
        channel.title = body.title
        if not body.auto_title:
            channel.title_manually_set = True
    if body.status is not None:
        channel.status = body.status
    if body.starred is not None:
        channel.starred = body.starred
    if body.master_agent is not None:
        channel.master_agent = body.master_agent
    if body.orchestration_mode is not None:
        mode = body.orchestration_mode.strip().lower()
        if mode not in ("dynamic", "master", "workflow"):
            return json_response(ResponseCode.BAD_REQUEST, "Invalid orchestration_mode")
        channel.orchestration_mode = mode
    if body.orchestration_instruction is not None:
        # Empty string clears the plan; otherwise store the trimmed text.
        channel.orchestration_instruction = body.orchestration_instruction.strip() or None
    if body.workflow_id is not None:
        wid = body.workflow_id.strip()
        channel.workflow_id = wid or None
        if wid:
            # Picking a workflow puts the thread in workflow mode and starts a
            # run (idempotent — an already-running run is left alone).
            channel.orchestration_mode = "workflow"
            from app.models import Workflow
            from app.services.workflow import get_active_run, start_run
            workflow = db.execute(
                select(Workflow).where(
                    Workflow.id == wid,
                    Workflow.workspace_id == workspace.id,
                )
            ).scalar_one_or_none()
            if workflow and (workflow.steps or []):
                db.flush()
                if get_active_run(db, str(workspace.id), channel.name) is None:
                    start_run(db, workspace, channel.name, workflow, prev_output="")

    db.commit()
    db.refresh(channel)
    return success_response(_format_channel(channel))


# ---------------------------------------------------------------------------
# DELETE /v1/workspaces/{workspace_id} — Delete workspace
# ---------------------------------------------------------------------------

@router.delete("/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Soft-delete a workspace (set status to 'deleted'). Requires workspace token or verified owner auth."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()

    if not workspace or workspace.status == "deleted":
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")

    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return _workspace_access_denied(authorization)

    workspace.status = "deleted"
    db.commit()

    return success_response({"workspaceId": str(workspace.id), "status": "deleted"})


@router.get("/{workspace_id}/me")
def get_me(
    workspace_id: str,
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Who am I in this workspace?

    A workspace has exactly one human — its owner — so there is no role to
    report, only whether the caller is that owner and whether they arrived on
    the machine token. The backend enforces every mutation independently; this
    is for display only."""
    workspace = db.execute(
        select(Workspace).where(_workspace_filter(workspace_id))
    ).scalar_one_or_none()
    if not workspace or workspace.status == "deleted":
        return json_response(ResponseCode.NOT_FOUND, "Workspace not found")
    if not verify_workspace_access(workspace, x_workspace_token, authorization, db=db):
        return _workspace_access_denied(authorization)

    token_access = bool(
        workspace.password_hash and x_workspace_token == workspace.password_hash
    )
    owner = is_workspace_owner(db, workspace, authorization)

    email = None
    display_name = None
    avatar_url = None
    if owner:
        user = resolve_current_user(db, authorization)
        if user is not None:
            email = user.email
            display_name = user.display_name
            avatar_url = user.avatar_url
            db.commit()  # persist the lazily created/refreshed User row

    return success_response({
        "email": email,
        "displayName": display_name,
        "avatarUrl": avatar_url,
        "authenticated": owner,
        "isOwner": owner,
        "tokenAccess": token_access,
    })


