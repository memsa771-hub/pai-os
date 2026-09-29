"""Workspace message processing and orchestration."""

import logging
from typing import Optional

from sqlalchemy import select

from app.eventing.events import Event
from app.eventing.handlers.agents import _validate_session
from app.eventing.handlers.routing import (
    _extract_mentions,
    _fallback_targets,
    _get_router_api_key,
    _master_targets,
    _online_participant_names,
    _post_system_notice,
    _route_with_llm,
)
from app.eventing.handlers.workflows import (
    TASK_CHANNEL_PREFIX,
    _handle_task_thread_progress,
)
from app.eventing.mods import PipelineContext

logger = logging.getLogger(__name__)

_DEFAULT_TITLES = {"New Thread", "Session 1", None, ""}


def _join_channel_as_human(channel, payload: dict, db) -> None:
    """Slack-style implicit join: the first time a human posts in a
    channel, add them to `channel_human_members` so future chat in this
    channel pushes to their devices. Idempotent — no-op when the row
    already exists. Needs `sender_email` on the payload; anonymous
    token-only visitors leave no membership trail and so don't get
    pushed for non-mention chat.
    """
    email = (payload.get("sender_email") or "").strip().lower()
    if not email or channel is None:
        return
    from app.models import ChannelHumanMember
    existing = db.execute(
        select(ChannelHumanMember).where(
            ChannelHumanMember.channel_id == channel.id,
            ChannelHumanMember.user_email == email,
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(ChannelHumanMember(channel_id=channel.id, user_email=email))
    db.flush()


def _auto_title_channel(channel, content: str, db) -> None:
    """Set channel title from message content if still using a default title."""
    if channel.title not in _DEFAULT_TITLES:
        return
    if not content or not content.strip():
        return
    # Use first line, truncated to 60 chars
    first_line = content.strip().split("\n")[0]
    title = first_line[:60].rstrip()
    if len(first_line) > 60:
        title += "..."
    channel.title = title
    db.flush()


async def _handle_message_posted(event: Event, ctx: PipelineContext) -> Optional[Event]:
    """
    workspace.message.posted → route messages to the right agents.

    Routing rules (human messages):
    - Starts with @agent-name → route to that agent only
    - No leading @mention → channel master (or all participants if no master)

    Routing rules (agent messages in multi-agent threads):
    - LLM router (Haiku) evaluates the last few messages and decides:
      - "next:agent-name" → route to that agent
      - "stop" → no targeting, conversation rests until human speaks
    - Fallback (single-agent threads or router disabled): no routing needed.
    """
    from app.models import Channel, WorkspaceMember

    db = ctx.extra["db"]
    workspace = ctx.extra["workspace"]
    payload = event.payload or {}
    content = payload.get("content", "")
    message_type = payload.get("message_type", "chat")

    # Reject posts from stale agent sessions. If the sender is an agent
    # and its claimed session_id does not match the current one in
    # WorkspaceMember, drop the event and flag it so the router can
    # return session_revoked to the client.
    if event.source and event.source.startswith("openagents:"):
        sender = event.source[len("openagents:"):]
        claimed_session = event.metadata.get("session_id") if event.metadata else None
        err = _validate_session(db, workspace.id, sender, claimed_session)
        if err == "session_revoked":
            event.metadata["session_error"] = err
            logger.info(
                "workspace_mod: rejected message from %s in %s (stale session_id)",
                sender, workspace.id,
            )
            # Return the event with the error flag but no content changes;
            # the router checks session_error and returns an error response.
            return event

    # "thinking", "status", and "todos" messages are intermediate agent output
    # — they should NOT trigger other agents.
    if message_type in ("thinking", "status", "todos"):
        return event

    # Parse @mentions from message content (used for human message routing)
    known_agents = [
        m.agent_name for m in db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
            )
        ).scalars().all()
    ]
    mentions = _extract_mentions(content, known_agents)

    # Resolve channel (needed for both agent and human message routing)
    channel = None
    if event.target.startswith("channel/"):
        channel_name = event.target[len("channel/"):]
        channel = db.execute(
            select(Channel).where(
                Channel.workspace_id == workspace.id,
                Channel.name == channel_name,
            )
        ).scalar_one_or_none()

    # Auto-name channel from first human message if title is default/empty
    if event.source.startswith("human:") and channel:
        _auto_title_channel(channel, content, db)
        # First post in *this* channel → auto-join so future non-mention
        # chat in the channel pushes to this human's devices.
        _join_channel_as_human(channel, event.payload or {}, db)

    # ── Workflow-driven channel: the workflow engine owns routing ──
    # When a WorkflowRun is active on this channel, a step-instruction message
    # (posted by the engine under a system: source) is targeted at the current
    # step's agent; every other message rests (the engine advances the run in a
    # background task after commit). This bypasses the generic router entirely.
    if channel is not None:
        from app.models import WorkflowRun
        wrun = db.execute(
            select(WorkflowRun).where(
                WorkflowRun.workspace_id == workspace.id,
                WorkflowRun.channel_name == channel.name,
                WorkflowRun.status == "running",
            )
        ).scalar_one_or_none()
        if wrun is not None:
            targets = ["__no_response__"]
            source = event.source or ""
            snap = wrun.snapshot or {}
            step = next((s for s in snap.get("steps", []) if s.get("id") == wrun.current_step), None)
            assignee = (step or {}).get("assignee") or {}
            step_agent = assignee.get("agent") if assignee.get("kind") == "agent" else None
            # Step instructions (system:workflow) target the step's agent. So
            # do HUMAN interjections during an agent step — "how's it going?"
            # must get an answer, not silence. The engine still only treats
            # the *agent's* replies as step output, so an interjection can't
            # accidentally advance the run. During a human step, the human's
            # message IS the step output and the engine consumes it.
            if step_agent and (source.startswith("system:") or source.startswith("human:")):
                targets = [step_agent]
                # Make sure the step's agent is a participant so it polls this channel.
                from app.models import ChannelMember
                existing = {p.agent_name for p in (channel.participants or [])}
                if step_agent not in existing:
                    db.add(ChannelMember(channel_id=channel.id, agent_name=step_agent))
                    db.flush()
            event.metadata["target_agents"] = targets
            return event

    # Skip non-human, non-agent sources
    if not event.source.startswith("human:") and not event.source.startswith("openagents:"):
        return event

    if not channel:
        # Direct message (target is an address, not a channel). Pin
        # target_agents explicitly: adapters treat an UN-targeted human
        # message as broadcast for legacy compat, so a DM without a target
        # list would wake every agent in the workspace. Agent recipients get
        # themselves; human recipients get the no-response sentinel.
        if not event.metadata.get("target_agents"):
            tgt = event.target or ""
            if tgt.startswith("openagents:"):
                event.metadata["target_agents"] = [tgt[len("openagents:"):]]
            elif tgt.startswith("human:"):
                event.metadata["target_agents"] = ["__no_response__"]
        return event

    # Online participants — used so routing prefers a live agent over one whose
    # daemon is down (an offline target just strands the message).
    online_names = _online_participant_names(db, workspace, channel)

    # ── Multi-agent channel: route per the thread's orchestration mode ──
    real_participants = [
        p for p in (channel.participants or [])
        if p.agent_name != "__no_response__"
    ]
    if len(real_participants) >= 2:
        from app.config import config
        mode = (getattr(channel, "orchestration_mode", None) or "dynamic").lower()

        if mode == "master":
            # Deterministic star topology — no LLM. If the channel somehow
            # has no master, fall back to the generic mention/online logic
            # so messages aren't stranded.
            if channel.master_agent:
                targets = _master_targets(event, channel, mentions)
            else:
                targets = _fallback_targets(event, channel, mentions, online_names)
        elif mode == "workflow" and config.ROUTER_LLM_ENABLED and _get_router_api_key():
            # LLM router steered by the user's natural-language plan.
            targets = await _route_with_llm(
                channel, event, db, workspace,
                workflow_instruction=getattr(channel, "orchestration_instruction", None),
            )
        elif config.ROUTER_LLM_ENABLED and _get_router_api_key():
            # "dynamic" (default) — generic LLM router.
            targets = await _route_with_llm(channel, event, db, workspace)
        else:
            # LLM router not available — fallback to mention or master.
            targets = _fallback_targets(event, channel, mentions, online_names)
    # ── Single-agent channel ────────────────────────────────────────
    else:
        targets = _fallback_targets(event, channel, mentions, online_names)

    # ALWAYS set target_agents, even when nobody should respond.
    #
    # Use a non-empty sentinel list ["__no_response__"] instead of []
    # because legacy clients (pre-0.2.106) check `!targets.length ||
    # targets.includes(agentName)` — an empty list is truthy-skipped
    # and falls through to broadcast, so every agent in the channel
    # replies at once. A non-empty list that contains no real agent
    # name causes old clients to reject (they fail the includes check)
    # and new clients to treat it as "nobody" (the sentinel is ignored).
    event.metadata["target_agents"] = targets if targets else ["__no_response__"]

    # Immediate heads-up when a human posts but NO agent in the thread is
    # online. Without this the client just spins on a reply that can't come.
    # The message is still recorded (and delivered to the target so it's picked
    # up on reconnect); this only adds a visible notice so the user isn't left
    # guessing. Skips routine channels and threads with no real agents.
    if (
        event.source.startswith("human:")
        and not channel.name.startswith("routines:")
        and real_participants
        and not online_names
    ):
        offline = ", ".join(f"@{p.agent_name}" for p in real_participants)
        _post_system_notice(
            db, workspace, channel.name,
            f"⚠️ No agent in this thread is online right now ({offline}). "
            f"Your message was saved and will be answered when an agent reconnects.",
            notice="no_agents_online",
        )

    # Auto-add targeted agents as channel participants so they can poll
    # for messages on this channel. Three guards:
    #   1. Never add the `__no_response__` sentinel — it's a routing
    #      signal, not a real agent.
    #   2. Only auto-add when the sender is a human. Agent→agent routing
    #      decisions (from the LLM router or master-fallback) used to
    #      drag bystander agents into channels they didn't belong in.
    #   3. Routine channels (`routines:<agent>`) are locked single-agent
    #      job queues — never add anyone but the owner.
    if event.source and event.source.startswith("human:") and \
            not channel.name.startswith("routines:"):
        from app.models import ChannelMember
        existing = {p.agent_name for p in (channel.participants or [])}
        # Clean up any __no_response__ sentinels that leaked into participants
        if "__no_response__" in existing:
            bogus = db.execute(
                select(ChannelMember).where(
                    ChannelMember.channel_id == channel.id,
                    ChannelMember.agent_name == "__no_response__",
                )
            ).scalar_one_or_none()
            if bogus:
                db.delete(bogus)
            existing.discard("__no_response__")
        for agent_name in event.metadata.get("target_agents", []):
            if agent_name == "__no_response__":
                continue
            if agent_name not in existing:
                db.add(ChannelMember(channel_id=channel.id, agent_name=agent_name))
                existing.add(agent_name)
        db.flush()

    # ── Kanban task threads: auto-move the card based on progress ──
    # A `task:<id>` channel is the working thread for a board task. The
    # assigned agent's replies (or a human unblocking it) drive its column.
    if channel.name.startswith(TASK_CHANNEL_PREFIX):
        try:
            _handle_task_thread_progress(event, channel, content, db, workspace)
        except Exception as e:  # never let board bookkeeping break message flow
            logger.error("Kanban task progress hook failed: %s", e)

    return event


# ---------------------------------------------------------------------------
# Handler dispatch table
# ---------------------------------------------------------------------------

