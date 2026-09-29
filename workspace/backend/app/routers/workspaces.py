# -*- coding: utf-8 -*-
"""
Workspace management endpoints — CRUD for the workspace itself.

These are NOT part of the ONM spec — they manage the product layer
(creating networks, listing user's workspaces, updating settings).

POST   /v1/workspaces              Create the signed-in student's workspace
GET    /v1/workspaces              List workspaces
GET    /v1/workspaces/{id}         Get workspace details
PATCH  /v1/workspaces/{id}         Update workspace settings
DELETE /v1/workspaces/{id}         Delete workspace
PATCH  /v1/workspaces/{id}/members/{name}  Update agent description/role
"""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.workspace import naming
from app.config import config
from app.database import get_db
from app.models import (
    Channel,
    ChannelMember,
    User,
    Workspace,
    WorkspaceMember,
)
from app.security.access import (
    is_workspace_owner,
    resolve_current_user,
    verify_workspace_access,
)
from app.api.response import ResponseCode, json_response, success_response
from app.routers.network import _workspace_filter
from app.services.pai import is_builtin_agent_type

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

    Thin wrapper over the single source of truth in app.security.access — kept here so
    the many callers importing this name don't have to change. (This path now
    accepts the same Supabase bearer as the network router.)
    """
    from app.security.access import verify_workspace_access
    return verify_workspace_access(workspace, token, authorization)


def _workspace_access_denied(authorization: Optional[str]):
    """Distinguish missing/invalid auth (401) from a valid non-member (403)."""
    bearer = _extract_bearer(authorization)
    if bearer:
        from app.security.human_auth import verify_identity_token
        if verify_identity_token(bearer):
            return json_response(ResponseCode.FORBIDDEN, "Workspace membership required")
    return json_response(ResponseCode.UNAUTHORIZED, "Invalid workspace credentials")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class WorkspaceCreateRequest(BaseModel):
    # `name` is accepted for compatibility but no longer decides anything: the
    # student's one workspace is provisioned by app.security.access.provision_workspace.
    name: Optional[str] = None

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
        if not is_builtin_agent_type(m.agent_type) and m.last_heartbeat:
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
            "builtin": is_builtin_agent_type(m.agent_type),
            "lastHeartbeatAt": m.last_heartbeat.isoformat() if m.last_heartbeat else None,
            "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
        })

    settings = ws.settings or {}
    return {
        "workspaceId": str(ws.id),
        "slug": ws.slug,
        "name": ws.name,
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
    """Create the signed-in student's personal workspace.

    Requires a verified identity. There is exactly one valid way a workspace
    comes into existence in hosted PAI — an authenticated user getting theirs —
    so this is idempotent: a user who already has one gets it back rather than
    a second. `owner_user_id` is set at creation time and never NULL.

    Anonymous creation is gone, and with it the "create first, claim later"
    lifecycle: an ownerless workspace had no human who could reach it (see
    app/access.py), so it could only ever have been an orphan.
    """
    from app.security.access import get_or_create_owned_workspace, resolve_current_user

    owner = resolve_current_user(db, authorization)
    if owner is None:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

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


# ---------------------------------------------------------------------------
# GET /v1/workspaces — List workspaces
# ---------------------------------------------------------------------------

@router.get("")
def list_workspaces(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """The signed-in student's workspace, as a one-element list.

    This used to take no credentials at all and return EVERY workspace in the
    deployment — names, slugs, creator emails and agent rosters — filterable by
    `creator_email`, which made it an enumeration endpoint for the whole tenant
    base. A student has exactly one workspace and no business seeing anyone
    else's, so the query is now scoped to the caller's own.
    """
    owner = resolve_current_user(db, authorization)
    if owner is None:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    workspaces = db.execute(
        select(Workspace)
        .where(Workspace.owner_user_id == owner.id, Workspace.status != "deleted")
        .options(selectinload(Workspace.members))
        .order_by(Workspace.last_activity_at.desc())
    ).scalars().all()
    db.commit()  # persist the lazily created/refreshed User row

    now = datetime.now(timezone.utc)
    return success_response([_format_workspace(ws, ws.members, now) for ws in workspaces])


# ---------------------------------------------------------------------------
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
        from app.security.access import verify_workspace_access
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
    # Display label, any script. Empty string clears it (falls back to agent_name).
    display_name: Optional[str] = None


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
    db.commit()

    return success_response({
        "agentName": member.agent_name,
        "displayName": member.display_name,
        "description": member.description,
        "role": member.role,
    })


# ---------------------------------------------------------------------------
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

