"""Channel creation and membership event handlers."""

from typing import Optional

from sqlalchemy import select

from app.eventing.events import Event
from app.eventing.mods import EventRejected, PipelineContext

async def _handle_channel_create(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.channel.create → create Channel + initial ChannelMember rows."""
    from app.models import Channel, ChannelMember, ChannelHumanMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    payload = event.payload or {}

    channel = Channel(
        workspace_id=workspace.id,
        name=payload.get("name", f"channel-{event.id[:8]}"),
        title=payload.get("title"),
        created_by=event.source,
        master_agent=payload.get("master"),
        resume_from=payload.get("resume_from"),
        status="active",
    )
    db.add(channel)
    db.flush()  # get channel.id

    # Add initial agent participants (filter out routing sentinels)
    participants = payload.get("participants", [])
    for agent_name in participants:
        if agent_name == "__no_response__":
            continue
        db.add(ChannelMember(channel_id=channel.id, agent_name=agent_name))

    # Add initial human participants as channel members, so push fan-out for
    # any message in this channel reaches their devices. These emails come from
    # the event payload and are NOT an authorization claim: channel membership
    # only decides who gets notified. Workspace access is decided solely by
    # owner_user_id or the machine token (see app/access.py).
    human_participants = payload.get("human_participants", []) or []
    for email_raw in human_participants:
        email = (email_raw or "").strip().lower()
        if not email or "@" not in email:
            continue
        existing_member = db.execute(
            select(ChannelHumanMember).where(
                ChannelHumanMember.channel_id == channel.id,
                ChannelHumanMember.user_email == email,
            )
        ).scalar_one_or_none()
        if not existing_member:
            db.add(ChannelHumanMember(channel_id=channel.id, user_email=email))

    db.flush()

    # Enrich event with created channel info
    event.metadata["channel_id"] = str(channel.id)
    event.metadata["channel_name"] = channel.name
    event.target = f"channel/{channel.name}"
    return event


def _is_channel_admin(event_source: str, channel, agent_name: str) -> bool:
    """Who is allowed to manage channel membership.

    Three sources are accepted:
      • a human user (`human:<name>`) — workspace clients act on behalf
        of the logged-in human
      • the channel's master agent (`openagents:<master>`) — owner can
        manage their own thread
      • the agent being added/removed itself (`openagents:<agent_name>`) —
        agents can join channels they've been invited to and leave on
        their own initiative
    """
    src = event_source or ""
    if src.startswith("human:"):
        return True
    if channel.master_agent and src == f"openagents:{channel.master_agent}":
        return True
    if src == f"openagents:{agent_name}":
        return True
    return False


async def _handle_channel_join(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.channel.join → add ChannelMember.

    Routine channels (`routines:<agent>`) are locked single-agent queues —
    we raise EventRejected so clients can roll back any optimistic UI.
    The owner is added in-line when the channel is first created (see
    app/routers/routines.py).
    """
    from app.models import Channel, ChannelMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    payload = event.payload or {}
    channel_name = payload.get("channel")
    agent_name = payload.get("agent_name")
    if not channel_name or not agent_name:
        return None

    if channel_name.startswith("routines:"):
        raise EventRejected(
            "workspace_mod",
            "routine_channel_locked: membership of routines:* is managed by the system",
        )

    channel = db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace.id,
            Channel.name == channel_name,
        )
    ).scalar_one_or_none()
    if not channel:
        raise EventRejected("workspace_mod", "channel_not_found")

    if not _is_channel_admin(event.source or "", channel, agent_name):
        raise EventRejected(
            "workspace_mod",
            "channel_join_forbidden: only humans, the channel master, or "
            "the agent being added may invite",
        )

    # Check if already a member
    existing = db.execute(
        select(ChannelMember).where(
            ChannelMember.channel_id == channel.id,
            ChannelMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if not existing:
        db.add(ChannelMember(channel_id=channel.id, agent_name=agent_name))
        # Auto-promote first agent to channel master if none set
        if not channel.master_agent:
            channel.master_agent = agent_name
        db.flush()

    return event


async def _handle_channel_leave(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """network.channel.leave → remove ChannelMember.

    Routine channels are locked — removing the owner is rejected so the
    queue keeps its single-agent invariant. Returns EventRejected so
    clients can roll back optimistic UI.
    """
    from app.models import Channel, ChannelMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    payload = event.payload or {}
    channel_name = payload.get("channel")
    agent_name = payload.get("agent_name")
    if not channel_name or not agent_name:
        return None

    if channel_name.startswith("routines:"):
        raise EventRejected(
            "workspace_mod",
            "routine_channel_locked: membership of routines:* is managed by the system",
        )

    channel = db.execute(
        select(Channel).where(
            Channel.workspace_id == workspace.id,
            Channel.name == channel_name,
        )
    ).scalar_one_or_none()
    if not channel:
        raise EventRejected("workspace_mod", "channel_not_found")

    if not _is_channel_admin(event.source or "", channel, agent_name):
        raise EventRejected(
            "workspace_mod",
            "channel_leave_forbidden: only humans, the channel master, or "
            "the agent being removed may leave",
        )

    member = db.execute(
        select(ChannelMember).where(
            ChannelMember.channel_id == channel.id,
            ChannelMember.agent_name == agent_name,
        )
    ).scalar_one_or_none()

    if member:
        db.delete(member)
        db.flush()

    return event


