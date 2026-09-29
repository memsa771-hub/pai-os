# -*- coding: utf-8 -*-
"""
PAI OS event envelope — the universal unit of workspace communication.

This module defines the event structure used by the PAI OS workspace.
Every interaction in a network is an event. There are no separate concepts for
"messages," "commands," or "notifications" — they are all events with different types.

It is intentionally independent of transport and persistence concerns.
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EventVisibility(str, Enum):
    """Determines who can see an event, even if routing would otherwise deliver it."""
    PUBLIC = "public"       # Any agent in the network
    NETWORK = "network"     # All members of the network
    CHANNEL = "channel"     # Only members of the target channel
    DIRECT = "direct"       # Only the target agent
    MOD_ONLY = "mod_only"   # Only mods in the pipeline (internal events)


class Event(BaseModel):
    """
    PAI OS event envelope — the single unit of communication.

    Every event has a source and a target. There are no null targets.
    To broadcast, use "agent:broadcast". To talk to the network, use "core".
    To reach a channel, use "channel/{name}".

    Event types are hierarchical, dot-separated strings following a
    {domain}.{entity}.{action} convention. Core events use the "network.*"
    namespace. Extensions use any other namespace (e.g., "workspace.*").
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str                       # e.g., "workspace.message.posted"
    source: str                     # e.g., "openagents:claude-agent"
    target: str                     # e.g., "channel/session-abc" (NEVER null)
    payload: Any = None             # Schema depends on type
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    network: str = ""               # Network ID where the event originated
    visibility: EventVisibility = EventVisibility.CHANNEL

    model_config = {"use_enum_values": True}

    @property
    def in_reply_to(self) -> Optional[str]:
        """Get the event ID this is a response to, if any."""
        return self.metadata.get("in_reply_to")


# PAI's workspace-message dispatch key. The wire event type stays unchanged.
PAI_MESSAGE_POSTED_EVENT_TYPE = "workspace.message.posted"
