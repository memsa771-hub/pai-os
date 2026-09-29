# -*- coding: utf-8 -*-
"""Durable in-app notifications.

Notifications are stored in the workspace inbox. Clients receive updates
through the existing API and realtime event channels.
"""

from app.models import NotificationRecord

REASON_APPROVAL = "approval"
REASON_TASK_COMPLETED = "task_completed"
REASON_ERROR = "error"


def notify(
    db,
    workspace_id: str,
    *,
    source: str,
    title: str,
    message: str,
    priority: str = "normal",
    channel_name: str | None = None,
    thread_id: str | None = None,
    link_url: str | None = None,
    reason: str | None = None,
    push: bool = True,
) -> NotificationRecord:
    """Create and flush an inbox notification without committing.

    ``reason`` and ``push`` remain accepted temporarily so existing producers
    do not need a synchronized deployment; neither controls an external push
    transport anymore.
    """
    del reason, push
    record = NotificationRecord(
        workspace_id=str(workspace_id),
        created_by=source,
        title=title,
        message=message,
        priority=priority or "normal",
        channel_name=channel_name,
        thread_id=thread_id,
        link_url=link_url,
    )
    db.add(record)
    db.flush()
    return record
