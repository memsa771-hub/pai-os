"""Message target selection and LLM-assisted routing."""

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from app.eventing.events import Event

logger = logging.getLogger(__name__)

def _extract_mentions(content: str, known_agents: List[str]) -> List[str]:
    """Parse @agent-name mentions from message text, validated against known agents."""
    if not content or not known_agents:
        return []
    # Match @word patterns (agent names are alphanumeric + hyphens)
    raw_mentions = re.findall(r"@([\w-]+)", content)
    # Only return mentions that match actual workspace members
    known_set = set(known_agents)
    return [m for m in raw_mentions if m in known_set]


def _extract_leading_mention(content: str, known_agents: List[str]) -> Optional[str]:
    """Return the agent name if the message starts with @agent-name, else None."""
    if not content or not known_agents:
        return None
    m = re.match(r"^\s*@([\w-]+)", content)
    if m and m.group(1) in set(known_agents):
        return m.group(1)
    return None


def _member_is_online(m) -> bool:
    """True when a WorkspaceMember is actually live.

    A crashed daemon leaves ``status='online'`` forever — the column is only
    flipped to 'offline' on a *clean* leave/heartbeat-timeout path — so the
    column alone is unreliable. We additionally require a fresh heartbeat,
    matching how /v1/discover and the participants list compute liveness. The
    built-in Counselor has no heartbeat loop, so its status column is trusted.
    """
    from app.config import config
    from datetime import timedelta
    if (m.status or "").lower() != "online":
        return False
    from app.services.pai import is_builtin_agent_type
    if is_builtin_agent_type(getattr(m, "agent_type", None)):
        return True
    hb = m.last_heartbeat
    if not hb:
        return False
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    timeout = timedelta(seconds=config.AGENT_TIMEOUT_SECONDS)
    return (datetime.now(timezone.utc) - hb) <= timeout


def _online_participant_names(db, workspace, channel) -> set:
    """Agent names of channel participants that are actually live (online column
    + fresh heartbeat). Routing prefers these so a thread whose previous agent
    went offline (e.g. a dead daemon) doesn't keep targeting it and stranding
    messages. Returns an empty set when status can't be resolved (callers then
    fall back to treating all participants as candidates)."""
    from app.models import WorkspaceMember
    names = [p.agent_name for p in (channel.participants or [])]
    if not names:
        return set()
    try:
        rows = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.agent_name.in_(names),
            )
        ).scalars().all()
        return {m.agent_name for m in rows if _member_is_online(m)}
    except Exception:
        return set()


def _post_system_notice(db, workspace, channel_name: str, content: str, notice: str) -> None:
    """Persist + publish a one-off system message into a channel so the user gets
    immediate feedback (e.g. "no agent online"). Bypasses the pipeline (no
    re-routing) and is committed with the current request transaction. Best-effort.
    """
    import uuid as _uuid
    import time as _time
    import json as _json
    from app.models import EventRecord
    from app.infrastructure import cache

    ev_id = str(_uuid.uuid4())
    ts = int(_time.time() * 1000)
    target = f"channel/{channel_name}"
    payload = {
        "content": content,
        "message_type": "chat",
        "sender_type": "system",
        "sender_name": "System",
    }
    metadata = {"target_agents": ["__no_response__"], "system_notice": notice}
    try:
        db.add(EventRecord(
            id=ev_id, network_id=workspace.id, type="workspace.message.posted",
            source="system:workspace", target=target, payload=payload,
            metadata_=metadata, timestamp=ts, visibility="channel",
        ))
        db.flush()
    except Exception:
        logger.exception("workspace_mod: failed to persist system notice")
        return
    try:
        cache.publish_event(
            f"ws:{workspace.id}:events",
            _json.dumps({
                "id": ev_id, "type": "workspace.message.posted",
                "source": "system:workspace", "target": target,
                "payload": payload, "metadata": metadata, "timestamp": ts,
            }, default=str, separators=(",", ":")).encode(),
        )
    except Exception:
        pass


def _fallback_targets(event, channel, mentions: List[str], online_names: set = None) -> List[str]:
    """Determine target agents when LLM router is unavailable.

    Priority: explicit @mentions → master (for human/member msgs) → online
    participant → any participant. When ``online_names`` is provided, an online
    participant is chosen over an offline one so messages aren't stranded on a
    dead agent. An explicit @mention is always honored as-is (the user chose it).
    """
    if mentions:
        return [mentions[0]]
    if channel.master_agent:
        if event.source.startswith("openagents:"):
            sender = event.source[len("openagents:"):]
            # Master's own messages: no self-trigger
            if sender == channel.master_agent:
                return []
        return [channel.master_agent]
    # No master — prefer an online participant, else the first participant.
    participants = [p.agent_name for p in (channel.participants or [])]
    if online_names:
        online_first = [p for p in participants if p in online_names]
        if online_first:
            return [online_first[0]]
    return [participants[0]] if participants else []


def _master_targets(event, channel, mentions: List[str]) -> List[str]:
    """Deterministic routing for "master" orchestration mode (star topology).

    The channel master is the single hub:
      • human message      → the master (the master owns the request and
        decides whether to answer or delegate)
      • sub-agent message  → back to the master (results always return to
        the hub, which decides the next hop)
      • master's message   → if it @mentions sub-agents, delegate to them;
        otherwise stop (the master answered the human directly)

    Falls back to nothing (empty → sentinel) when the channel has no master;
    callers may substitute the generic fallback in that case.
    """
    master = channel.master_agent
    if not master:
        return []

    source = event.source or ""
    if source.startswith("openagents:"):
        sender = source[len("openagents:"):]
        if sender == master:
            # Master is delegating. Route to any mentioned sub-agents
            # (never itself); no mention → the master answered, so stop.
            participants = {p.agent_name for p in (channel.participants or [])}
            delegated = [
                m for m in mentions if m != master and m in participants
            ]
            return delegated
        # A sub-agent spoke → return control to the master hub.
        return [master]

    # Human (or system) message → always the master.
    return [master]


def _prompt_inline(text: str) -> str:
    """Flatten user-controlled text for safe inline use in the router prompt.

    Display names and descriptions come from users; control characters or
    Unicode line/paragraph separators in them could forge extra
    participant/instruction lines. Delegates to the shared Unicode-aware
    sanitizer (Cc/Zl/Zp + bidi controls → spaces).
    """
    from app.workspace.naming import sanitize_inline
    return sanitize_inline(text)


_ROUTER_PROMPT = """\
You are a conversation router for a multi-agent workspace. Decide which \
agent should respond next to the LATEST message. Use judgment — read the \
message carefully and think about who is actually being addressed.

Channel participants:
{participants}
Master agent: {master}
{plan}
Recent conversation (oldest → newest):
{history}

LATEST message from {sender}:
{content}

HOW TO DECIDE:

A. Identify who (if anyone) is being directly addressed.
   Treat @agent-name as ADDRESSING that agent only when the agent is the \
subject being asked to do/say something. If the agent is merely referenced \
("check @Alice's note, Bob" — Alice is referred to, Bob is addressed), \
pick the addressed agent, not the mentioned one.

B. If the LATEST message is from a HUMAN:
   - Always pick exactly one agent. Humans expect a reply — never output \
"stop" for a human message.
   - Prefer whoever is directly addressed.
   - If nobody is directly addressed, check CONVERSATIONAL CONTINUITY: \
if the user was just conversing with a specific agent (the last agent \
reply was from agent X, or X asked the user a question that this message \
appears to answer), continue with that agent X.
   - Otherwise pick the agent whose role/description best fits the topic; \
fall back to the master agent.

C. If the LATEST message is from an AGENT:
   - If it delegates or hands off to another agent ("@Alice please do X", \
"Alice, could you check X"), route to that agent.
   - If it reports back to the master or asks the master to decide, route to the master.
   - If it is a FINAL answer to the previous human question or an \
acknowledgement ("done", "saved", "sounds good"), output "stop".
   - Never route back to the same agent that just spoke (no self-loops).
   - When unsure, prefer "stop" to avoid infinite agent-to-agent loops.

EXAMPLES:
  Human: "@alice what's the status?"                → next:alice
  Human: "check @alice's notes, @bob"                → next:bob       (bob is addressed)
  Human: "how about julia?"  (julia is not an agent) → next:<master>  (who owns that topic)
  Agent alice: "@bob can you verify?"                → next:bob
  Agent alice: "Done — results attached."            → stop
  Agent bob (master): "Here's the final answer ..."  → stop

  Conversational continuity examples:
    alice: "I'm here. What do you need?"
    Human: "do you know about X?"                    → next:alice     (continuing with alice)

    alice: "I pulled these results: [...]."
    Human: "thanks, can you also check Y?"           → next:alice     (follow-up to alice)

Output EXACTLY one line, lowercase, no punctuation or explanation:
  next:<agent_name>
  stop"""


def _get_router_api_key() -> str:
    """Use PAI's single server-managed inference credential."""
    from app.config import config
    return config.PAI_API_KEY


def _get_router_model() -> str:
    """Use PAI's configured model for turn routing."""
    from app.config import config
    return config.PAI_MODEL


async def _route_with_llm(
    channel, new_event: Event, db, workspace, workflow_instruction: Optional[str] = None
) -> List[str]:
    """Use a small LLM to decide which agent(s) should respond next.

    Returns a list of agent names to target, or an empty list (stop).
    Falls back to empty list on any error.

    ``workflow_instruction`` (set in "workflow" orchestration mode) is a
    user-authored natural-language collaboration plan. When present it is
    injected into the prompt as the authoritative routing policy, so the
    same router engine steers the thread according to the user's plan
    instead of the generic heuristics.
    """
    from app.config import config
    from app.models import EventRecord

    if not _get_router_api_key():
        logger.warning("LLM router: PAI_API_KEY is not configured; defaulting to deterministic routing")
        return []

    # Fetch last 5 chat messages from this channel
    channel_target = f"channel/{channel.name}"
    recent = db.execute(
        select(EventRecord)
        .where(
            EventRecord.network_id == workspace.id,
            EventRecord.target == channel_target,
            EventRecord.type == "workspace.message.posted",
        )
        .order_by(EventRecord.timestamp.desc())
        .limit(5)
    ).scalars().all()

    # Build conversation history (oldest first)
    recent.reverse()
    history_lines = []
    for evt in recent:
        payload = evt.payload or {}
        msg_type = payload.get("message_type", "chat")
        if msg_type in ("thinking", "status"):
            continue
        source = evt.source
        if source.startswith("human:"):
            label = "human"
        elif source.startswith("openagents:"):
            label = source[len("openagents:"):]
        else:
            label = source
        text = (payload.get("content") or "")[:500]  # Truncate long messages
        history_lines.append(f"[{_prompt_inline(label)}] {text}")

    history = "\n".join(history_lines) if history_lines else "(no prior messages)"

    # Participant list with role/description for better routing
    from app.models import WorkspaceMember
    participant_names = [p.agent_name for p in (channel.participants or [])]
    members = {
        m.agent_name: m for m in db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.agent_name.in_(participant_names),
            )
        ).scalars().all()
    }
    # Only offer ONLINE participants to the router when any are online — an
    # offline agent (dead daemon) can't reply, so routing to it by
    # conversational continuity just strands the message. If none are online,
    # present all participants (the chosen one will pick the message up when it
    # reconnects).
    online_set = {
        n for n in participant_names
        if members.get(n) and _member_is_online(members[n])
    }
    candidate_names = [n for n in participant_names if n in online_set] if online_set else participant_names
    # Every field below is user-controlled at some entry point (legacy rows
    # predate the join-time character policy), so flatten them all — a value
    # must never span lines or the prompt structure can be forged.
    participant_lines = []
    for name in candidate_names:
        m = members.get(name)
        role = _prompt_inline(str(m.role)) if m and m.role else "member"
        desc = _prompt_inline(m.description) if m and m.description else ""
        line = f"  - {_prompt_inline(name)} (role: {role})"
        # Users may address an agent by its display name ("小明, 帮我看下")
        # rather than its ASCII agent name — give the router the alias. The
        # output contract stays next:<agent_name>.
        alias = _prompt_inline(m.display_name) if m and m.display_name else ""
        if alias and alias != name:
            line += f" (also known as: {alias})"
        if desc:
            line += f" — {desc}"
        participant_lines.append(line)
    participants_str = "\n".join(participant_lines) if participant_lines else "  (none)"

    master = _prompt_inline(channel.master_agent) or "(none)"
    sender = new_event.source
    if sender.startswith("openagents:"):
        sender = sender[len("openagents:"):]
    sender = _prompt_inline(sender)

    content = (new_event.payload or {}).get("content", "")[:500]

    # In "workflow" mode, the user-authored plan is the authoritative routing
    # policy. Injected as its own block so the model weighs it above the
    # generic heuristics below.
    plan = ""
    if workflow_instruction and workflow_instruction.strip():
        plan = (
            "\nCOLLABORATION PLAN (authoritative — follow this exactly when "
            "deciding who speaks next; it overrides the generic guidance "
            "below):\n"
            f"{workflow_instruction.strip()}\n"
        )

    prompt = _ROUTER_PROMPT.format(
        participants=participants_str,
        master=master,
        plan=plan,
        history=history,
        sender=sender,
        content=content,
    )

    try:
        from app.inference.client import chat_completion

        model = _get_router_model()
        raw_result = (await chat_completion(
            api_key=config.PAI_API_KEY,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            base_url=config.PAI_BASE_URL or None,
        )).strip()

        # Case-insensitive keyword detection but preserve original case
        # of the agent name so we can match it against participants
        # (agent names are case-sensitive in the workspace).
        result = raw_result.lower()

        logger.info("LLM router decision: %s (channel=%s, sender=%s)", raw_result, channel.name, sender)

        if result.startswith("next:"):
            # Preserve the original case from the model output so we can
            # match against participant names, which ARE case-sensitive.
            agent_name = raw_result[len("next:"):].strip().split(",")[0].strip()
            # Case-insensitive participant lookup, then canonicalize to
            # the stored case.
            # Validate against the candidate set (online participants when any
            # are online) so the router can't route to an offline agent while a
            # live one is available.
            participants_by_lower = {
                name.lower(): name for name in candidate_names
            }
            canonical = participants_by_lower.get(agent_name.lower())
            if canonical is None:
                logger.warning(
                    "LLM router returned unknown agent: %r (valid: %s)",
                    agent_name, list(participants_by_lower.values()),
                )
                # For human senders, fall through to the safety net below
                # so the user always gets a reply.
                if not (new_event.source or "").startswith("human:"):
                    return []
                agent_name = None
            else:
                agent_name = canonical
                # Reject self-loops — router sometimes picks the agent
                # who just spoke. Sender's adapter skips own messages but
                # legacy clients would still see the target and retry.
                if (new_event.source or "").startswith("openagents:"):
                    sender = new_event.source[len("openagents:"):]
                    if agent_name == sender:
                        logger.info("LLM router self-loop rejected: %s", sender)
                        return []
                return [agent_name]
        else:
            agent_name = None  # "stop" or unrecognized

        # Safety net: humans ALWAYS get a response. If the router said
        # "stop" (or returned an invalid agent) for a human message,
        # fall back to the master/fallback target. Without this, the
        # router can silently drop a legitimate follow-up question like
        # "how about Julia?" after a previous "final answer" message.
        if (new_event.source or "").startswith("human:"):
            fallback = _fallback_targets(new_event, channel, [], online_set)
            if fallback:
                logger.info(
                    "LLM router returned stop/invalid for human message — "
                    "routing to fallback %s instead", fallback,
                )
                return fallback
        return []

    except Exception as e:
        logger.error("LLM router failed, defaulting to fallback: %s", e)
        # Same safety net on exception: humans still get a reply.
        if (new_event.source or "").startswith("human:"):
            try:
                fallback = _fallback_targets(new_event, channel, [], online_set)
                if fallback:
                    return fallback
            except Exception:
                pass
        return []


# ---------------------------------------------------------------------------
# Kanban task progress classification
#
# A task's working thread is a `task:<id>` channel. When the assigned agent
# posts a chat message there, we run the same fast model used for routing to
# decide whether the card should move to Need Input, Done, or stay In Progress.
# ---------------------------------------------------------------------------

