"""Workspace event dispatch mod.

Domain behavior lives in :mod:`app.eventing.handlers`; this module keeps the
pipeline-facing mod stable for ``app.eventing.factory``.
"""

from typing import List, Optional

from app.eventing.events import Event, PAI_MESSAGE_POSTED_EVENT_TYPE
from app.eventing.handlers.agents import (
    _handle_agent_join,
    _handle_agent_leave,
    _handle_agent_remove,
    _handle_ping,
)
from app.eventing.handlers.channels import (
    _handle_channel_create,
    _handle_channel_join,
    _handle_channel_leave,
)
from app.eventing.handlers.messages import _handle_message_posted
from app.eventing.mods import PipelineContext, TransformMod


class WorkspaceMod(TransformMod):
    """Dispatch workspace events to domain-specific handlers."""

    name = "workspace"
    intercepts: List[str] = []
    priority = 50

    async def process(self, event: Event, context: PipelineContext) -> Optional[Event]:
        handler = _HANDLERS.get(event.type)
        if handler:
            return await handler(event, context)
        return event


_HANDLERS = {
    "network.agent.join": _handle_agent_join,
    "network.agent.leave": _handle_agent_leave,
    "network.agent.remove": _handle_agent_remove,
    "network.ping": _handle_ping,
    "network.channel.create": _handle_channel_create,
    "network.channel.join": _handle_channel_join,
    "network.channel.leave": _handle_channel_leave,
    PAI_MESSAGE_POSTED_EVENT_TYPE: _handle_message_posted,
}
