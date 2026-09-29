"""Workflow and Kanban task-thread progression helpers."""

import logging

from sqlalchemy import select

from app.eventing.events import Event
from app.eventing.handlers.routing import (
    _get_router_api_key,
    _get_router_model,
    _prompt_inline,
)

logger = logging.getLogger(__name__)

TASK_CHANNEL_PREFIX = "task:"

_TASK_CLASSIFIER_PROMPT = """\
You are tracking a long-running task on a Kanban board. An agent is working on \
the task in a thread. From the task and the agent's LATEST message, decide the \
task's current column.

Task title: {title}
Task description:
{description}

Recent thread (oldest → newest):
{history}

Agent's LATEST message:
{content}

Choose EXACTLY ONE status:
- done         → the task is fully complete; the agent delivered the final \
result and nothing remains to do.
- need_input   → the agent is blocked and needs a human to decide, clarify, \
approve, or provide something (credentials, a choice, missing info) before it \
can continue.
- in_progress  → the agent is still actively working or reporting intermediate \
progress.

Be conservative: pick "done" only when the work is clearly finished, and \
"need_input" only when the agent explicitly needs a human to act. When unsure, \
pick "in_progress".

Output EXACTLY one line, lowercase, no punctuation or explanation:
status:done
status:need_input
status:in_progress"""


def _next_task_position(db, workspace, status: str) -> int:
    """Append a card to the bottom of the target Kanban column."""
    from app.models import KanbanTask
    rows = db.execute(
        select(KanbanTask.position).where(
            KanbanTask.workspace_id == str(workspace.id),
            KanbanTask.status == status,
        )
    ).scalars().all()
    return (max(rows) + 1) if rows else 0


def _classify_task_progress(task, latest_content: str, db, workspace) -> str:
    """Classify a task's column from the assigned agent's latest message.

    Returns one of ``in_progress`` | ``need_input`` | ``done``. Falls back to
    ``in_progress`` when no LLM is configured or on any error, so a failure
    never strands a card in the wrong column (the user can still drag it).
    """
    from app.config import config
    from app.models import EventRecord

    if not (config.ROUTER_LLM_ENABLED and _get_router_api_key()):
        return "in_progress"

    channel_target = f"channel/{task.channel_name}"
    recent = db.execute(
        select(EventRecord)
        .where(
            EventRecord.network_id == workspace.id,
            EventRecord.target == channel_target,
            EventRecord.type == "workspace.message.posted",
        )
        .order_by(EventRecord.timestamp.desc())
        .limit(6)
    ).scalars().all()
    recent.reverse()

    history_lines = []
    for evt in recent:
        payload = evt.payload or {}
        if payload.get("message_type", "chat") in ("thinking", "status", "todos"):
            continue
        source = evt.source or ""
        if source.startswith("human:"):
            label = "human"
        elif source.startswith("openagents:"):
            label = source[len("openagents:"):]
        else:
            label = source
        text = (payload.get("content") or "")[:500]
        history_lines.append(f"[{label}] {text}")
    history = "\n".join(history_lines) if history_lines else "(no prior messages)"

    prompt = _TASK_CLASSIFIER_PROMPT.format(
        title=task.title,
        description=(task.description or "(none)")[:1000],
        history=history,
        content=(latest_content or "")[:1000],
    )

    try:
        from app.inference.client import _token_limit_kwarg, create_sync_client

        client = create_sync_client(
            config.PAI_API_KEY, base_url=config.PAI_BASE_URL or None,
        )
        model = _get_router_model()
        try:
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                _token_limit_kwarg(model): 15,
            }
            resp = client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content.strip()
        finally:
            client.close()
        result = raw.lower()
        logger.info("Task classifier: %s (task=%s)", raw, task.id)
        if "need_input" in result:
            return "need_input"
        if "done" in result:
            return "done"
        return "in_progress"
    except Exception as e:
        logger.error("Task classifier failed, staying in_progress: %s", e)
        return "in_progress"


def _notify_task_transition(task, new_status: str, db, workspace) -> None:
    """Notify when a card lands in Need Input or Done.

    Both states are things a person is waiting on away from the app — a card
    that cannot proceed without them, and work they asked for finishing — so
    both reach the phone as well as the inbox. Need Input travels under
    `approval`: it is the state that literally blocks on a human.
    """
    from app.services.notify import REASON_APPROVAL, REASON_TASK_COMPLETED, notify

    if new_status == "need_input":
        title = "Task needs your input"
        message = f"“{task.title}” is blocked and needs your input."
        priority = "high"
        reason = REASON_APPROVAL
    elif new_status == "done":
        title = "Task completed"
        who = task.assignee or "an agent"
        message = f"“{task.title}” was completed by {who}."
        priority = "normal"
        reason = REASON_TASK_COMPLETED
    else:
        return

    notify(
        db,
        str(workspace.id),
        source=(f"openagents:{task.assignee}" if task.assignee else "system:kanban"),
        title=title,
        message=message,
        priority=priority,
        channel_name=task.channel_name,
        reason=reason,
    )


def _handle_task_thread_progress(event: Event, channel, content: str, db, workspace) -> None:
    """Move a Kanban card based on activity in its `task:<id>` thread.

    - The assigned agent's chat message → classify into in_progress / need_input
      / done and move the card (with an inbox notification on the terminal-ish
      transitions).
    - A human reply while the card is in Need Input → resume: back to In
      Progress (the human just unblocked the agent).
    """
    from app.models import KanbanTask

    task = db.execute(
        select(KanbanTask).where(
            KanbanTask.workspace_id == workspace.id,
            KanbanTask.channel_name == channel.name,
        )
    ).scalar_one_or_none()
    if not task or task.status == "done":
        return

    source = event.source or ""
    if source.startswith("openagents:"):
        sender_name = source[len("openagents:"):]
        # Only the assigned agent's own replies drive the card.
        if task.assignee and sender_name == task.assignee:
            new_status = _classify_task_progress(task, content, db, workspace)
            if new_status != task.status:
                task.status = new_status
                task.position = _next_task_position(db, workspace, new_status)
                _notify_task_transition(task, new_status, db, workspace)
    elif source.startswith("human:") and task.status == "need_input":
        # A human answered the blocker → let the agent resume.
        task.status = "in_progress"
        task.position = _next_task_position(db, workspace, "in_progress")


