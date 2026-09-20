# -*- coding: utf-8 -*-
"""Build the (small) context an extractor sees for one turn.

Deliberately NOT the Counselor's chat window. That window is tuned for holding
a conversation — 100 messages, 60k chars — and feeding it to an extractor would
re-propose the same facts on every turn, cost a fortune, and bury the one thing
that actually changed.

What the extractor gets instead:

    the user message          full — the primary evidence
    the PAI reply             bounded — for reference resolution only
    a few prior turns         bounded — to resolve "that country", "same budget"
    the Vault snapshot        so it can tell a correction from a restatement
    a few existing memories   so it does not re-propose what we already know

Everything is loaded from PostgreSQL by event ID, so the job row stays small.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from app.models import EventRecord

logger = logging.getLogger(__name__)

# Bounds. Small on purpose — see module docstring.
MAX_USER_CHARS = 4000
MAX_ASSISTANT_CHARS = 2000
RECENT_TURN_COUNT = 4
MAX_RECENT_CHARS = 400
MAX_EXISTING_MEMORIES = 15


@dataclass
class TurnContext:
    """One completed turn, plus just enough surrounding state."""

    workspace_id: str
    user_event_id: str
    user_text: str
    assistant_event_id: Optional[str] = None
    assistant_text: str = ""
    recent: list[dict] = field(default_factory=list)      # [{role, text}]
    vault: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)
    existing_memories: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.user_text.strip()


def _text_of(event: EventRecord) -> str:
    return ((event.payload or {}).get("content") or "").strip()


def _is_chat(event: EventRecord) -> bool:
    """Real conversation only — not thinking/status/todo chatter."""
    return ((event.payload or {}).get("message_type") or "chat") == "chat"


def _is_workspace_owner_source(db, workspace_id: str, source: Optional[str]) -> bool:
    """True only for the one human who owns this workspace."""
    from app.models import Workspace

    if not source or not source.startswith("human:"):
        return False
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.owner_user_id is None:
        return False
    return source == f"human:{workspace.owner_user_id}"


def build_turn_context(
    db,
    workspace_id: str,
    user_event_id: str,
    assistant_event_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[TurnContext]:
    """Load one turn from durable storage. None if the user event is missing.

    The workspace predicate on every read is what keeps one student's turn from
    ever pulling another student's rows into an extraction prompt.
    """
    user_event = db.execute(
        select(EventRecord).where(
            EventRecord.id == user_event_id,
            EventRecord.network_id == workspace_id,
        )
    ).scalar_one_or_none()
    if user_event is None:
        logger.warning(
            "memory: user event %s not found in workspace %s", user_event_id, workspace_id
        )
        return None

    # Only the workspace OWNER's own words become the student's remembered
    # truth. Event sources are server-derived now (app/event_identity.py), so
    # the owner is exactly `human:<owner_user_id>` and this is a real check
    # rather than a naming convention. It matters because `human:` is a
    # namespace, not a person: an external Slack/Telegram sender arrives as
    # `human:<platform>-<name>` (app/services/integrations.py), and if a
    # binding's default_agent is PAI Counselor, that outsider's messages would
    # otherwise be extracted as things the student said about themselves.
    if not _is_workspace_owner_source(db, workspace_id, user_event.source):
        logger.info(
            "memory: skipping extraction for non-owner source %s in workspace %s",
            user_event.source, workspace_id,
        )
        return None

    context = TurnContext(
        workspace_id=workspace_id,
        user_event_id=user_event_id,
        user_text=_text_of(user_event)[:MAX_USER_CHARS],
    )

    if assistant_event_id:
        assistant_event = db.execute(
            select(EventRecord).where(
                EventRecord.id == assistant_event_id,
                EventRecord.network_id == workspace_id,
            )
        ).scalar_one_or_none()
        if assistant_event is not None:
            context.assistant_event_id = assistant_event_id
            context.assistant_text = _text_of(assistant_event)[:MAX_ASSISTANT_CHARS]

    target = channel or user_event.target
    if target:
        prior = db.execute(
            select(EventRecord).where(
                EventRecord.network_id == workspace_id,
                EventRecord.target == target,
                EventRecord.type == "workspace.message.posted",
                EventRecord.timestamp < user_event.timestamp,
            ).order_by(EventRecord.timestamp.desc()).limit(RECENT_TURN_COUNT * 2)
        ).scalars().all()
        for event in reversed(prior):
            if not _is_chat(event):
                continue
            text = _text_of(event)
            if not text:
                continue
            context.recent.append({
                "role": "student" if event.source.startswith("human:") else "assistant",
                "text": text[:MAX_RECENT_CHARS],
            })
        context.recent = context.recent[-RECENT_TURN_COUNT:]

    # Current canonical state, so the extractor can distinguish a correction
    # ("actually 3.52") from a restatement ("my CGPA is 3.41" again) and skip
    # what we already hold.
    from .semantic import MemoryService
    from .vault import VaultService

    context.vault = VaultService(db).snapshot(workspace_id, include_sensitive=True)
    from .student_records import StudentRecordService
    context.records = StudentRecordService(db).snapshot(workspace_id, limit=12)
    context.existing_memories = [
        m.content
        for m in MemoryService(db).list_memories(workspace_id, limit=MAX_EXISTING_MEMORIES)
    ]
    return context
