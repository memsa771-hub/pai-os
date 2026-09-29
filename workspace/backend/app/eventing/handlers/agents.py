"""Agent lifecycle and presence event handlers."""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.eventing.events import Event
from app.eventing.mods import EventRejected, PipelineContext

logger = logging.getLogger(__name__)

async def _handle_agent_join(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.agent.join → upsert WorkspaceMember, set online, rotate session."""
    import uuid as _uuid
    from app.models import WorkspaceMember

    from app.workspace import naming

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    agent_name = event.payload.get("agent_name") if event.payload else None
    if not agent_name:
        logger.warning("workspace_mod: agent.join missing agent_name in payload")
        return None

    # agent_name is inserted verbatim into router prompts and participant
    # lists — apply the shared character policy here (post-auth), covering
    # /v1/join and raw /v1/events alike.
    name_problem = naming.agent_name_problem(agent_name)
    if name_problem:
        logger.info(
            "workspace_mod: refused join in %s — %s", workspace.id, name_problem,
        )
        event.metadata["reject_reason"] = "invalid_agent_name"
        event.metadata["reject_detail"] = f"Invalid agent name: {name_problem}"
        raise EventRejected("workspace_mod", "invalid_agent_name")

    # Take the namespace lock BEFORE reading membership: two concurrent joins
    # of the same name could otherwise both see existing=None and collide on
    # the primary key instead of the second one rotating the session.
    naming.lock_member_namespace(db, workspace.id)

    existing = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    # A removed agent must not resurrect itself by re-joining. Its daemon
    # reconnects and re-POSTs /v1/join, which would otherwise upsert the row
    # back to status='online' — exactly the "removed agent still appears" bug.
    # Re-adding a retired member is an explicit human action.
    # Raising EventRejected makes _emit_event return None → /v1/join 401s.
    # (issue #347)
    if existing and existing.status == "removed":
        logger.info(
            "workspace_mod: refused re-join of removed agent %s in %s",
            agent_name, workspace.id,
        )
        raise EventRejected("workspace_mod", "agent_removed")

    now = datetime.now(timezone.utc)

    agent_type = event.payload.get("agent_type") if event.payload else None

    # Rotate session on every join. Any prior client holding the old
    # session_id (ghost adapter, duplicate daemon) gets rejected when it
    # next heartbeats or posts, which tells it to stop.
    new_session_id = _uuid.uuid4().hex

    if existing:
        prior_session = existing.session_id
        existing.status = "online"
        existing.last_heartbeat = now
        existing.session_id = new_session_id
        existing.session_started_at = now
        if agent_type and not existing.agent_type:
            existing.agent_type = agent_type
        if prior_session and prior_session != new_session_id:
            logger.info(
                "workspace_mod: rotated session for %s in %s (prior session revoked)",
                agent_name, workspace.id,
            )
    else:
        # New member: its agent_name enters the shared name/alias namespace,
        # so it must not equal another member's display_name. Runs after
        # AuthMod, under the namespace lock taken above.
        clash = naming.find_alias_clash(
            db, workspace.id, agent_name, exclude_agent=agent_name,
        )
        if clash:
            logger.info(
                "workspace_mod: refused join of %s in %s — clashes with display name of %s",
                agent_name, workspace.id, clash,
            )
            event.metadata["reject_reason"] = "display_name_conflict"
            event.metadata["reject_detail"] = (
                f"Agent name '{agent_name}' conflicts with the display name "
                f"of member '{clash}'"
            )
            raise EventRejected("workspace_mod", "display_name_conflict")

        # Role is caller-supplied (raw /v1/events can claim anything,
        # including non-strings that would make the frozenset lookup throw) —
        # whitelist it so it can't smuggle text into prompts or grant an
        # unknown role.
        role = event.payload.get("role", "member")
        if not isinstance(role, str) or role not in naming.ALLOWED_ROLES:
            role = "member"
        member = WorkspaceMember(
            workspace_id=workspace.id,
            agent_name=agent_name,
            role=role,
            agent_type=agent_type,
            status="online",
            last_heartbeat=now,
            session_id=new_session_id,
            session_started_at=now,
        )
        db.add(member)

    workspace.last_activity_at = now
    db.flush()

    # Enrich event metadata with resolved info + session_id so the
    # router returns it to the joining client.
    event.metadata["role"] = existing.role if existing else role
    event.metadata["network_id"] = str(workspace.id)
    event.metadata["session_id"] = new_session_id
    return event


def _validate_session(db, workspace_id, agent_name: str, claimed_session: Optional[str]) -> Optional[str]:
    """Check that claimed_session matches the current session for this agent.

    Returns None if valid or legacy (nothing to enforce), else an error code
    string ("session_revoked" | "session_missing") that callers can surface.

    Semantics:
      - stored=None      → legacy member, accept anything (transition)
      - stored=X, claim=None → legacy client, accept (transition)
      - stored=X, claim=X → valid
      - stored=X, claim=Y → revoked: another client joined as this agent
    """
    from app.models import WorkspaceMember

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()
    if not member or not member.session_id:
        return None  # legacy or not-yet-joined
    if not claimed_session:
        return None  # legacy client that hasn't learned session_id yet
    if claimed_session != member.session_id:
        return "session_revoked"
    return None


async def _handle_agent_leave(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.agent.leave → set member offline."""
    from app.models import WorkspaceMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    agent_name = event.payload.get("agent_name") if event.payload else None
    if not agent_name:
        return None

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not member:
        return None

    member.status = "offline"
    db.flush()
    return event


async def _handle_agent_remove(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.agent.remove → delete WorkspaceMember, reassign master if needed."""
    from app.models import Channel, WorkspaceMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    agent_name = event.payload.get("agent_name") if event.payload else None
    if not agent_name:
        logger.warning("workspace_mod: agent.remove missing agent_name in payload")
        return None

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not member:
        return None

    was_master = member.role == "master"
    # Soft-delete: keep the row (status='removed') so a re-add can reactivate
    # the agent and so _handle_agent_join can refuse to resurrect it. Hard-
    # deleting left nothing to stop a still-running daemon from re-joining and
    # upserting the membership back to online — the removed agent reappeared.
    # (issue #347)
    member.status = "removed"
    db.flush()

    new_master_name = None

    # If removed agent was master, promote the next available (non-removed) agent
    if was_master:
        next_master = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.status != "removed",
            ).order_by(WorkspaceMember.joined_at.asc())
        ).scalar_one_or_none()

        if next_master:
            next_master.role = "master"
            new_master_name = next_master.agent_name
            db.flush()

    # Reassign channel masters: any channel where removed agent was master
    channels = db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace.id,
            Channel.master_agent == agent_name,
        )
    ).scalars().all()

    for ch in channels:
        ch.master_agent = new_master_name
    db.flush()

    event.metadata["removed_agent"] = agent_name
    if new_master_name:
        event.metadata["new_master"] = new_master_name
    return event


async def _handle_ping(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.ping → update heartbeat timestamp.

    Validates session_id if the client sent one. A mismatch means a newer
    client has joined as this agent; we drop this heartbeat and mark the
    event metadata so the caller can surface session_revoked to the
    stale client, which will then stop.
    """
    from app.models import WorkspaceMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    agent_name = event.payload.get("agent_name") if event.payload else None
    if not agent_name:
        return None

    claimed_session = (event.payload or {}).get("session_id")
    err = _validate_session(db, workspace.id, agent_name, claimed_session)
    if err == "session_revoked":
        event.metadata["session_error"] = err
        logger.info(
            "workspace_mod: rejected heartbeat for %s in %s (stale session_id)",
            agent_name, workspace.id,
        )
        return event

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not member:
        return None

    # A removed agent's still-running daemon keeps heartbeating; without this
    # guard the line below would flip it back to status='online'. Surface
    # session_revoked so the caller stops its adapter for this agent. (issue #347)
    if member.status == "removed":
        event.metadata["session_error"] = "session_revoked"
        logger.info(
            "workspace_mod: rejected heartbeat for removed agent %s in %s",
            agent_name, workspace.id,
        )
        return event

    now = datetime.now(timezone.utc)
    member.status = "online"
    member.last_heartbeat = now
    db.flush()
    return event


