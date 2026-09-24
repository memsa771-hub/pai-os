# -*- coding: utf-8 -*-
"""
Workspace ORM models.

Aligned with the ONM: events table as the core log, plus materialized state
tables for efficient queries.

Uses both Python-side `default=` and PostgreSQL `server_default=` so models
work in SQLite (tests) and PostgreSQL (production).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core event store
# ---------------------------------------------------------------------------

class EventRecord(Base):
    """
    Persisted ONM event. Every interaction is stored as an event row.
    Populated by mod/persistence.
    """
    __tablename__ = "events"

    id = Column(Text, primary_key=True)                     # ULID or UUID
    network_id = Column(UUID(as_uuid=False), nullable=False)  # workspace ID
    type = Column(Text, nullable=False)                      # e.g. "workspace.message.posted"
    source = Column(Text, nullable=False)                    # e.g. "openagents:claude-agent"
    target = Column(Text, nullable=False)                    # e.g. "channel/session-abc"
    payload = Column(JSONB)
    metadata_ = Column("metadata", JSONB, default={})        # underscore to avoid Python keyword
    timestamp = Column(BigInteger, nullable=False)           # unix ms
    visibility = Column(Text, default="channel")
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_events_network_type", "network_id", "type"),
        Index("idx_events_network_target", "network_id", "target"),
        Index("idx_events_network_timestamp", "network_id", "timestamp"),
        Index("idx_events_network_type_target_ts", "network_id", "type", "target", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Materialized state tables (projections maintained by mods)
# ---------------------------------------------------------------------------

class Workspace(Base):
    """A workspace = an ONM network.

    Placement AI (v2.0, single-owner): a student's product-facing workspace is
    identified by `owner_user_id`, not by membership rows. `uq_workspace_owner_active`
    (a partial unique index on `owner_user_id` where `status = 'active'`) is what
    actually guarantees "one user owns at most one active personal workspace" —
    enforced in the database so concurrent first-logins can't create two. See
    `app.access.get_or_create_owned_workspace`.

    `owner_user_id` is nullable: legacy/anonymous/agent-created and machine-only
    workspaces have no human owner, and a user's old extra workspaces (from
    before this model existed) are intentionally left with no owner rather than
    merged or deleted — see migration 053's backfill notes.
    """
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    slug = Column(Text, unique=True)
    name = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=True)
    # The student who owns this as their one personal workspace. NULL for
    # machine-only/legacy workspaces and for a user's non-canonical extra
    # legacy workspaces (kept for data safety, not reachable via the normal
    # product — see migration 053).
    owner_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Retained for clients that read it. Human access no longer depends on it:
    # a human is allowed iff they are the owner (see app/access.py). Agents and
    # daemons always authenticate with the workspace token regardless.
    require_login = Column(Boolean, nullable=False, default=True, server_default=text("TRUE"))
    settings = Column(JSONB, default={})
    status = Column(Text, default="active")
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    last_activity_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    members = relationship("WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan")
    channels = relationship("Channel", back_populates="workspace", cascade="all, delete-orphan")
    invitations = relationship("Invitation", back_populates="workspace", cascade="all, delete-orphan")

    __table_args__ = (
        # The actual "one active personal workspace per user" guarantee —
        # database-enforced so two concurrent first-logins can't both win a
        # race and create two owned workspaces. Partial: doesn't constrain
        # machine-only workspaces (owner_user_id NULL) or archived/deleted ones.
        Index(
            "uq_workspace_owner_active", "owner_user_id", unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL AND status = 'active'"),
            sqlite_where=text("owner_user_id IS NOT NULL AND status = 'active'"),
        ),
        Index("idx_workspace_owner", "owner_user_id"),
    )


class WorkspaceMember(Base):
    """Agent membership in a workspace (network membership)."""
    __tablename__ = "workspace_members"

    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(Text, nullable=False)
    # Free-form label shown in UIs (any script, incl. CJK); agent_name stays
    # the ASCII identity used for mentions, routing and storage keys.
    display_name = Column(Text, nullable=True)
    role = Column(Text, default="member")           # master | member | observer
    agent_type = Column(Text, nullable=True)          # "claude", "openclaw", etc.
    server_host = Column(Text, nullable=True)          # hostname/IP where agent runs
    # The device the agent runs on, stamped at join time when the join was
    # authenticated with that node's token. Nullable: cloud agents and
    # manual-token joins have no node.
    working_dir = Column(Text, nullable=True)          # working directory on the server
    description = Column(Text, nullable=True)           # user-provided description of agent's role/capabilities
    enabled_skills = Column(JSONB, nullable=True)      # {"files": true, "browser": false, ...} — null = all defaults
    model = Column(Text, nullable=True)                  # user-picked model id; null = agent's own default
    status = Column(Text, default="offline")         # online | offline
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    joined_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    # Opaque token assigned on each /v1/join. Subsequent heartbeats and
    # message posts must carry this id; a newer join rotates it so any
    # stale client (e.g. ghost adapter, second daemon on same config)
    # posting with the old id gets rejected and stops.
    session_id = Column(Text, nullable=True)
    session_started_at = Column(DateTime(timezone=True), nullable=True)

    workspace = relationship("Workspace", back_populates="members")

    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "agent_name"),
    )


class Channel(Base):
    """A channel = session / thread (named event stream)."""
    __tablename__ = "channels"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)              # e.g. "session-{uuid}"
    title = Column(Text, nullable=True)
    title_manually_set = Column(Boolean, default=False, server_default=text("FALSE"))
    created_by = Column(Text, nullable=True)
    master_agent = Column(Text, nullable=True)       # per-channel master
    resume_from = Column(Text, nullable=True)         # channel name to resume context from
    # Multi-agent collaboration mode for this thread:
    #   "dynamic"  → LLM router picks next speaker (generic prompt) [default]
    #   "master"   → deterministic star: humans + sub-agents route to the
    #                master; the master delegates via @mention
    #   "workflow" → a structured Workflow template drives the thread step by
    #                step (see workflow_id + the workflow_runs table)
    orchestration_mode = Column(Text, nullable=False, server_default=text("'dynamic'"))
    # Legacy free-text collaboration plan — superseded by structured workflows
    # (kept for backward compat; no longer authored in the UI).
    orchestration_instruction = Column(Text, nullable=True)
    # Structured workflow selected for this thread ("workflow" mode). The live
    # run lives in workflow_runs, keyed by this channel's name.
    workflow_id = Column(Text, nullable=True)
    status = Column(Text, default="active")           # active | archived | deleted
    starred = Column(Boolean, default=False, server_default=text("FALSE"))
    last_event_at = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    workspace = relationship("Workspace", back_populates="channels")
    participants = relationship("ChannelMember", back_populates="channel", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        Index("uq_channels_ws_name", "workspace_id", "name", unique=True),
        # Serves /v1/discover's `WHERE workspace_id = ? AND status != 'deleted'`.
        Index("idx_channels_workspace_status", "workspace_id", "status"),
        # Serves the timer-loop auto-archive scan
        # (`status = 'active' AND last_event_at < cutoff`).
        Index("idx_channels_status_last_event", "status", "last_event_at"),
    )


class ChannelMember(Base):
    """Per-channel participant (per-thread membership)."""
    __tablename__ = "channel_members"

    channel_id = Column(UUID(as_uuid=False), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(Text, nullable=False)

    channel = relationship("Channel", back_populates="participants")

    __table_args__ = (
        PrimaryKeyConstraint("channel_id", "agent_name"),
    )


class ChannelHumanMember(Base):
    """Per-channel human participant — Slack-style thread membership.

    Lives alongside `ChannelMember` (agents only) rather than mixing
    `agent_name` + `user_email` into one row, which would muddy the
    existing agent routing queries. Auto-populated by the workspace mod
    on first human post in a channel; consulted by `services/push.py` to
    decide whose devices get a banner for non-mention chat messages.
    Mentions still wake the mentioned human regardless of membership.
    """
    __tablename__ = "channel_human_members"

    channel_id = Column(UUID(as_uuid=False), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    user_email = Column(Text, nullable=False)               # normalized lowercase
    joined_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        PrimaryKeyConstraint("channel_id", "user_email"),
        Index("idx_channel_human_members_email", "user_email"),
    )


class Invitation(Base):
    """Workspace invitation."""
    __tablename__ = "invitations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    target_agent = Column(Text, nullable=False)
    invite_token = Column(Text, nullable=False, unique=True)
    status = Column(Text, default="pending")         # pending | accepted | rejected | expired
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    expires_at = Column(DateTime(timezone=True), nullable=False)

    workspace = relationship("Workspace", back_populates="invitations")



class User(Base):
    """A human end-user identity, resolved from a verified login-provider
    access token (Supabase Auth — the canonical human-identity provider — or
    Sign in with Apple, used by the iOS app).

    Distinct from `WorkspaceMember`, which represents AGENTS and is keyed by
    agent_name. A user reaches exactly one workspace: the one whose
    `owner_user_id` is their id. There is no membership table, no role and no
    second human — see app/access.py.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    email = Column(Text, nullable=False)                 # normalized lowercase
    supabase_uid = Column(Text, nullable=True)           # Supabase auth.users `id`
    apple_sub = Column(Text, nullable=True)              # Sign in with Apple `sub` claim
    # Unique login handle, normalized lowercase — authentication only (not a
    # profile/display name). Lets "sign in with username" resolve to an email
    # server-side (see app/routers/auth.py) without Supabase's own API, which
    # only takes email/phone.
    username = Column(Text, nullable=True)
    display_name = Column(Text, nullable=True)
    # User-set profile picture: an https:// URL or a small data:image/... URL
    # (the frontend downscales uploads client-side before saving).
    avatar_url = Column(Text, nullable=True)
    # Has this account dismissed the first-run welcome? Per-account (not
    # per-device) so mobile onboarding shows exactly once across devices.
    welcome_seen = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    # When this account finished (or dismissed) first-run onboarding. A
    # timestamp rather than a flag so "when did students start completing it?"
    # stays answerable. NULL = the onboarding form is still owed.
    onboarded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    last_login_at = Column(DateTime(timezone=True), nullable=True)


    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("uq_users_username_lower", func.lower(username), unique=True,
              postgresql_where=username.isnot(None)),
    )



class KnowledgeEntry(Base):
    """A knowledge base entry — workspace-global markdown document."""
    __tablename__ = "knowledge_entries"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    slug = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    storage_key = Column(Text, nullable=True)
    content_size = Column(Integer, nullable=True)
    created_by = Column(Text, nullable=False)
    updated_by = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_knowledge_workspace_slug"),
        Index("idx_knowledge_workspace_status", "workspace_id", "status"),
    )


# ---------------------------------------------------------------------------
# Shared file storage
# ---------------------------------------------------------------------------

class FileRecord(Base):
    """Metadata for a file stored in the workspace."""
    __tablename__ = "files"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    filename = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False, default="application/octet-stream")
    size = Column(Integer, nullable=False)
    storage_key = Column(Text, nullable=False)
    uploaded_by = Column(Text, nullable=False)        # "human:user" or "openagents:agent-name"
    channel_name = Column(Text, nullable=True)         # optional channel context
    status = Column(Text, nullable=False, default="active")  # active | deleted
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    # Trash. A deleted record keeps its bytes until it's purged; these three
    # columns are what turn "status = deleted" into something restorable.
    #   deleted_at  when it went to the trash (and what an expiry sweep reads)
    #   trash_id    one delete action — deleting a folder trashes N records
    #               that must come back, or be purged, together
    #   trash_path  what the user deleted: a file's path, or a folder's
    # All nullable: records deleted before trash existed simply have none.
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    trash_id = Column(Text, nullable=True)
    trash_path = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_files_workspace_status", "workspace_id", "status"),
        Index("idx_files_trash", "workspace_id", "trash_id"),
    )


# ---------------------------------------------------------------------------
# Shared browser
# ---------------------------------------------------------------------------

class BrowserTab(Base):
    """A shared browser tab in the workspace."""
    __tablename__ = "browser_tabs"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False, default="about:blank")
    title = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="active")       # active | closed
    created_by = Column(Text, nullable=False)                      # "human:user" or "openagents:agent-name"
    shared_with = Column(JSONB, default=[])                        # list of agent names with access
    context_id = Column(Text, ForeignKey("browser_contexts.id", ondelete="SET NULL"), nullable=True)  # persistent context
    session_id = Column(Text, nullable=True)                       # Browserbase session ID
    live_url = Column(Text, nullable=True)                         # Browserbase live view URL
    # --- BF credential reference (never the key itself; see app/browser_creds.py) ---
    bf_key_source = Column(Text, nullable=True)                    # 'workspace' | 'global' | NULL (local/legacy)
    bf_key_fingerprint = Column(Text, nullable=True)               # SHA-256 hex of the creating key
    # --- Remote session release tracking ---
    session_closed = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))  # BF session confirmed released
    close_status = Column(Text, nullable=False, default="none", server_default=text("'none'"))  # none|open|closing|closed|close_failed|retry_exhausted
    close_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_close_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_close_error = Column(Text, nullable=True)                 # redacted — never contains key material
    last_error = Column(Text, nullable=True)                       # last init/navigation error (redacted)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    last_active_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_browser_tabs_workspace_status", "workspace_id", "status"),
    )


# ---------------------------------------------------------------------------
# Persistent browser contexts (BrowserBase contexts for session persistence)
# ---------------------------------------------------------------------------

class BrowserContext(Base):
    """A persistent browser context that preserves cookies/storage across sessions.

    Users mark a tab as persistent by giving it a name (e.g. "LinkedIn Account").
    A BrowserBase context is created and reused across tab open/close cycles,
    so the logged-in state survives indefinitely.
    """
    __tablename__ = "browser_contexts"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)                          # user-provided label, e.g. "LinkedIn Account"
    bb_context_id = Column(Text, nullable=True)                  # BrowserBase context ID (null in local mode)
    domain = Column(Text, nullable=True)                         # auto-captured from tab URL, e.g. "linkedin.com"
    status = Column(Text, nullable=False, default="active")      # active | expired
    created_by = Column(Text, nullable=False)                    # "human:user" or "openagents:agent-name"
    shared_with = Column(JSONB, default=[])                      # list of agent names that can use this context
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    last_used_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_browser_context_workspace_name"),
        Index("idx_browser_contexts_workspace_status", "workspace_id", "status"),
    )


# ---------------------------------------------------------------------------
# Browser usage tracking
# ---------------------------------------------------------------------------

class BrowserUsage(Base):
    """Tracks browser session duration for billing/monitoring."""
    __tablename__ = "browser_usage"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    tab_id = Column(Text, nullable=False)
    session_id = Column(Text, nullable=True)             # Browserbase session ID
    opened_by = Column(Text, nullable=False)               # source: "human:user" or "openagents:agent-name"
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now, server_default=text("NOW()"))
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)     # computed on close

    __table_args__ = (
        Index("idx_browser_usage_workspace", "workspace_id"),
        Index("idx_browser_usage_opened_by", "opened_by"),
        Index("idx_browser_usage_started", "started_at"),
    )


# ---------------------------------------------------------------------------
# Push-notification device registration
# ---------------------------------------------------------------------------

class DeviceToken(Base):
    """A mobile device's FCM registration token, scoped to a workspace.

    Created by `POST /v1/devices/register` from the OpenAgents mobile apps.
    Used by `services/push.py` to fan out notifications through Firebase
    Cloud Messaging when relevant workspace events fire — iOS and Android
    alike; `device_type` is descriptive, not a transport selector.

    Tied to a workspace via `workspace_id` — the same auth model as every
    other table here. We do not link to a specific human user because the
    workspace token is the only identity the iOS client carries today.
    """

    __tablename__ = "device_tokens"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    fcm_token = Column(Text, nullable=False)
    device_type = Column(Text, nullable=False)            # "ios" | future: "android" | "macos"
    bundle_id = Column(Text, nullable=True)               # e.g. "com.openagents.go"
    # Google email of the signed-in user on the device that registered.
    # NULL for older clients without a user identity; populated by builds
    # that started sending `userEmail` with /v1/devices/register. The push
    # fan-out filters by this column when a @-mention resolves to a human
    # workspace owner so only that specific human's devices get woken up.
    user_email = Column(Text, nullable=True)
    # Notification switches as set on the device's Notifications screen —
    # {approvals, mentions, agentErrors, taskCompletions, allMessages,
    # quietHours}, all booleans. Mirrored server-side because a banner the
    # OS draws while the app is dead can only be stopped by not sending it.
    # NULL means "registered before this existed" and is treated as all-on.
    prefs = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    last_seen_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("workspace_id", "fcm_token", name="uq_device_token_workspace_fcm"),
        Index("idx_device_tokens_workspace", "workspace_id"),
        Index("idx_device_tokens_workspace_user", "workspace_id", "user_email"),
    )


# ---------------------------------------------------------------------------
# Planning: To-dos & Timers
# ---------------------------------------------------------------------------

class TodoRecord(Base):
    """A single to-do item belonging to an agent in a channel."""
    __tablename__ = "todos"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(Text, nullable=False)
    thread_id = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)              # "openagents:agent-name"
    assignee = Column(Text, nullable=False)                # defaults to created_by agent
    content = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | in_progress | completed
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_todos_workspace_channel", "workspace_id", "channel_name"),
        Index("idx_todos_workspace_created_by", "workspace_id", "created_by"),
    )


class KanbanTask(Base):
    """A Kanban board task — workspace-wide, assignable to a single agent.

    Distinct from ``TodoRecord`` (agent-private, in-thread planning
    checklists). A Kanban task is a GitHub-issue-like work item on a shared
    board. Assigning it to an agent spins up a dedicated *hidden* thread
    (a ``task:<id>`` channel) where the agent does the long-running work; a
    fast-model classifier watches the agent's replies there and moves the
    card between columns (``in_progress`` → ``need_input`` / ``done``).
    """
    __tablename__ = "kanban_tasks"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    # backlog | todo | in_progress | need_input | done
    status = Column(Text, nullable=False, default="backlog", server_default="backlog")
    assignee = Column(Text, nullable=True)                 # bare agent name; null = unassigned
    # A task runs on either a single agent (assignee) OR a workflow template.
    workflow_id = Column(Text, nullable=True)             # run this task via a Workflow
    created_by = Column(Text, nullable=False)              # "human:..." or "openagents:..."
    channel_name = Column(Text, nullable=True)            # the hidden `task:<id>` thread, once assigned
    priority = Column(Text, nullable=False, default="normal", server_default="normal")  # low | normal | high
    position = Column(Integer, nullable=False, default=0, server_default="0")  # ordering within a column
    # Knowledge-base entries attached as context (list of KnowledgeEntry ids).
    # Referenced in the kickoff as @knowledge:<slug> so agents fetch them.
    knowledge_ids = Column(JSONB, nullable=True)
    # Files attached to the task (list of FileRecord ids). Delivered as
    # attachments on the kickoff message so the agent can open them.
    file_ids = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_kanban_workspace_status", "workspace_id", "status"),
        Index("idx_kanban_workspace_channel", "workspace_id", "channel_name"),
    )


class Workflow(Base):
    """A reusable multi-agent collaboration template.

    A workflow is an ordered list of steps (stored as JSON). Each step has an
    instruction and an assignee (an agent or a named human), and an optional
    natural-language **gate** — "go to step X if <condition>" — that the fast
    model judges, enabling forward skips and backward loops.

    step := {
      "id": str, "name": str, "instruction": str,
      "assignee": {"kind": "agent"|"human", "agent"?: str, "human"?: str},
      "gate"?: {"condition": str, "target": <step id>}   # else falls through
    }

    Running a task/thread copies this template into a ``WorkflowRun`` snapshot,
    so later edits never disturb work already in flight.
    """
    __tablename__ = "workflows"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    steps = Column(JSONB, nullable=False)                  # ordered list of step dicts
    # Loop budget: how many times the run may cycle before it stalls. The engine
    # also enforces a hard backstop of max_iterations * len(steps) activations.
    max_iterations = Column(Integer, nullable=False, default=5, server_default="5")
    created_by = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_workflows_workspace", "workspace_id"),
    )


class WorkflowRun(Base):
    """Live execution state for a workflow driving one thread (channel).

    Serves both a Kanban task and a group-chat thread — whichever owns the
    ``channel_name``. Holds the frozen template ``snapshot`` and the cursor
    (``current_step`` + ``iterations``).
    """
    __tablename__ = "workflow_runs"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    workflow_id = Column(Text, nullable=True)              # origin template (reference only)
    channel_name = Column(Text, nullable=False)            # the thread this run drives
    snapshot = Column(JSONB, nullable=False)               # {name, steps, max_iterations}
    current_step = Column(Text, nullable=True)             # step id, or null before start / after end
    iterations = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(Text, nullable=False, default="running", server_default="running")  # running | done | stalled | cancelled
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_workflow_runs_ws_channel", "workspace_id", "channel_name"),
    )


class ExecutionRun(Base):
    """Live state for one PAI Operator execution — the hidden execution
    intelligence PAI Counselor delegates to (see app/services/operator.py).

    Deliberately flat (no separate step table yet): ``plan`` is a JSON list of
    ``{"id", "title", "status"}`` objects — real plan progress ("3/5 steps
    complete"), not a name for whatever tool the model happened to call.
    Tool-call history is a genuinely different concept (every action taken,
    not the objective's semantic steps) and is tracked separately in
    ``tool_calls`` so the two are never conflated again. A future step table
    can be added without touching this one.
    """
    __tablename__ = "execution_runs"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    # Who asked for this — "openagents:pai" for PAI Counselor today; any
    # future caller (a workflow step, a human action) fits the same column.
    requested_by = Column(Text, nullable=False)
    # The event target ("channel/<thread-id>") the objective was delegated
    # from — where the finished result gets posted back to automatically, the
    # same way a normal cloud-agent reply is (see _post_response). Nullable
    # because a delegate call without a live thread (tests, future non-chat
    # callers) simply has nowhere to auto-post to.
    channel_target = Column(Text, nullable=True)
    objective = Column(Text, nullable=False)
    constraints = Column(JSONB, nullable=True)              # e.g. {"do_not_submit_without_approval": true}
    context_refs = Column(JSONB, nullable=True)              # e.g. ["student_vault", "application_123"]
    # pending -> understanding -> planning -> executing -> verifying -> one of:
    #   completed | needs_user_action | failed
    status = Column(Text, nullable=False, default="pending", server_default="pending")
    current_step = Column(Text, nullable=True)
    plan = Column(JSONB, nullable=True)                      # ordered [{"id","title","status"}, ...]
    completed_steps = Column(JSONB, nullable=True)           # titles of `plan` entries with status == completed
    tool_calls = Column(JSONB, nullable=True)                # raw action history: [{"tool","ok"}, ...] — NOT plan progress
    missing = Column(JSONB, nullable=True)                   # what verification found incomplete
    approval_required_for = Column(Text, nullable=True)      # e.g. "final_submission"
    error = Column(Text, nullable=True)
    # The durable result of the run — the actual source of truth even if
    # posting it back into chat fails. Shape is caller-defined (e.g. a
    # research summary with findings/sources, or an application's completed
    # field count) — flexible on purpose, this is not a 200-char summary.
    result = Column(JSONB, nullable=True)
    # The raw VERIFY-phase output (status/missing/approval/summary/whatever
    # else that pass produced) — kept in full alongside the flattened
    # `missing`/`approval_required_for` columns above for quick querying.
    verification = Column(JSONB, nullable=True)
    result_type = Column(Text, nullable=True)                 # e.g. "text", "research", "application"
    result_artifact_id = Column(Text, nullable=True)           # e.g. a generated FileRecord.id, when applicable
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_execution_runs_workspace", "workspace_id"),
        Index("idx_execution_runs_workspace_status", "workspace_id", "status"),
    )


class TimerRecord(Base):
    """A scheduled timer that posts a message when it fires."""
    __tablename__ = "timers"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(Text, nullable=False)
    thread_id = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)              # "openagents:agent-name"
    message = Column(Text, nullable=False)
    delay_seconds = Column(Integer, nullable=False)
    fires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(Text, nullable=False, default="active")  # active | fired | cancelled
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_timers_fires_at_status", "fires_at", "status"),
        Index("idx_timers_workspace_channel", "workspace_id", "channel_name"),
    )


# ---------------------------------------------------------------------------
# Routines (recurring scheduled tasks)
# ---------------------------------------------------------------------------

class RoutineRecord(Base):
    """A recurring scheduled task that fires on a repeating schedule."""
    __tablename__ = "routines"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(Text, nullable=False)
    thread_id = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)              # "openagents:agent-name"
    name = Column(Text, nullable=False)                     # human-readable label
    message = Column(Text, nullable=False)                  # message posted when routine fires
    context = Column(Text, nullable=True)                    # comprehensive background for the routine
    # Daily schedule mode: hour + minute (+ optional days). One of the two
    # modes must be set when the row is created (enforced in the router).
    schedule_hour = Column(Integer, nullable=True)          # 0-23 UTC
    schedule_minute = Column(Integer, nullable=True)        # 0-59
    schedule_days = Column(JSONB, nullable=True)            # null=every day, or [0..6] (0=Mon)
    # Interval mode: fire every N minutes. Mutually exclusive with hour/minute.
    schedule_interval_minutes = Column(Integer, nullable=True)
    timezone = Column(Text, default="UTC")
    next_fires_at = Column(DateTime(timezone=True), nullable=False)
    last_fired_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Text, nullable=False, default="active")  # active | paused | cancelled
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_routines_workspace_channel", "workspace_id", "channel_name"),
        Index("idx_routines_next_fires_status", "next_fires_at", "status"),
    )


# ---------------------------------------------------------------------------
# Inbox / Notifications
# ---------------------------------------------------------------------------

class NotificationRecord(Base):
    """A notification sent by an agent to the workspace inbox."""
    __tablename__ = "notifications"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(Text, nullable=False)              # "openagents:agent-name" or "system:routine"
    title = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(Text, nullable=False, default="normal")  # low | normal | high
    is_read = Column(Boolean, default=False, server_default=text("FALSE"))
    channel_name = Column(Text, nullable=True)              # optional link to related thread
    thread_id = Column(Text, nullable=True)
    link_url = Column(Text, nullable=True)                  # optional external link
    status = Column(Text, nullable=False, default="active") # active | dismissed | expired
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_notifications_workspace_status", "workspace_id", "status"),
        Index("idx_notifications_workspace_read", "workspace_id", "is_read"),
        Index("idx_notifications_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Cloud agent configurations
# ---------------------------------------------------------------------------

class CloudAgentConfig(Base):
    """Configuration for a cloud-based agent (API-proxied by the server)."""
    __tablename__ = "cloud_agent_configs"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)              # "openai", "google", "xai", "deepseek"
    model = Column(Text, nullable=False)                  # "gpt-4o", "gemini-2.5-pro", etc.
    category = Column(Text, nullable=False, default="chat")  # "chat" or "image"
    api_key = Column(Text, nullable=False)
    base_url = Column(Text, nullable=True)                # custom OpenAI-compatible endpoint
    system_prompt = Column(Text, nullable=True)
    max_tokens = Column(Integer, nullable=True)
    status = Column(Text, nullable=False, default="active")  # active | disabled
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_name", name="uq_cloud_agent_workspace_name"),
        Index("idx_cloud_agent_workspace", "workspace_id"),
    )


# ---------------------------------------------------------------------------
# Chat-platform integrations (Slack / Telegram bridges)
# ---------------------------------------------------------------------------

class IntegrationBinding(Base):
    """A connection between this workspace and an external chat platform bot.

    One binding = one bot (a Telegram bot from BotFather, or a Slack app's bot
    user). Each external conversation the bot participates in is bridged to a
    dedicated workspace channel named ``ext-<platform>-<binding8>-<chat id>``
    (deterministic — no per-conversation mapping table). Inbound platform
    messages flow through the normal event pipeline as ``human:`` sources, so
    routing/leader/mention logic applies unchanged; outbound agent ``chat``
    replies are relayed back by ``services/integrations.relay_for_event``.
    """
    __tablename__ = "integration_bindings"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    platform = Column(Text, nullable=False)               # "telegram" | "slack"
    name = Column(Text, nullable=True)                    # display label, e.g. bot username
    bot_token = Column(Text, nullable=False)              # Telegram bot token / Slack xoxb- token
    signing_secret = Column(Text, nullable=True)          # Slack request-signing secret (custom apps only)
    webhook_secret = Column(Text, nullable=True)          # Telegram X-Telegram-Bot-Api-Secret-Token
    # Slack team id. The official OpenAgents Slack app delivers every team's
    # events to ONE shared endpoint — this column is how an event finds its
    # binding (indexed; also set for custom Slack apps, unused by Telegram).
    external_team_id = Column(Text, nullable=True)
    # Route every bridged message to this agent (becomes master_agent of the
    # auto-created channels). Null = let the router/leader logic decide.
    default_agent = Column(Text, nullable=True)
    config = Column(JSONB, default=dict)                  # {botUsername, teamName, botUserId, ...}
    status = Column(Text, nullable=False, default="active")  # active | disabled
    last_error = Column(Text, nullable=True)              # last relay/webhook failure (redacted)
    last_event_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Text, nullable=True)              # email of the admin who connected it
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_integration_bindings_workspace", "workspace_id"),
        Index("idx_integration_bindings_team", "external_team_id"),
    )


# ---------------------------------------------------------------------------
# Shared conversation snapshots
# ---------------------------------------------------------------------------

class ShareSnapshot(Base):
    """A public snapshot of a conversation thread."""
    __tablename__ = "share_snapshots"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    created_by = Column(Text, nullable=False)
    snapshot_data = Column(JSONB, nullable=False)
    share_token = Column(Text, unique=True, nullable=False)
    message_count = Column(Integer, nullable=False, default=0)
    status = Column(Text, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_share_snapshots_workspace", "workspace_id"),
        Index("idx_share_snapshots_token", "share_token"),
    )


# Standalone agent table (used when IDENTITY_MODE=standalone)
class Agent(Base):
    """Local agent identity (standalone mode only)."""
    __tablename__ = "agents"

    agent_name = Column(Text, primary_key=True)
    display_name = Column(Text, nullable=True)
    agent_type = Column(Text, nullable=True)         # "claude", "codex", "gemini", etc.
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))


class ModelAccess(Base):
    """A saved inference credential ("model access") for a workspace.

    One entry = provider + API key (+ optional custom base URL). Managed on the
    Model access settings page and reused across agent configs: forms reference
    the entry by id, the backend resolves the real key server-side (node-command
    enqueue, probes), so the raw key never has to round-trip via the browser
    after creation.
    """
    __tablename__ = "model_access"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    label = Column(Text, nullable=False)               # display name, defaults to provider label
    provider = Column(Text, nullable=False)            # PROVIDERS name, or "custom"
    base_url = Column(Text, nullable=True)             # custom/relay endpoint override
    api_key = Column(Text, nullable=False)             # stored server-side; only masked form is listed
    created_by = Column(Text, nullable=True)           # email or identity of the creator
    status = Column(Text, default="active")            # active | disabled
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))


class Feedback(Base):
    """In-app user feedback — bug reports and feature requests.

    Stored first (queryable, can't get lost), then best-effort forwarded by
    email (FEEDBACK_EMAIL_TO). workspace_id is advisory context, not an access
    grant, so it carries no FK; user_email is denormalized so feedback stays
    readable if the account is later deleted.
    """
    __tablename__ = "feedback"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email = Column(Text, nullable=True)
    workspace_id = Column(Text, nullable=True)
    kind = Column(Text, nullable=False)                 # bug | feature | other
    message = Column(Text, nullable=False)
    context = Column(JSONB, nullable=True)              # {url, userAgent, locale, ...}
    status = Column(Text, nullable=False, default="new", server_default=text("'new'"))  # new | triaged | closed
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))


# ---------------------------------------------------------------------------
# PAI Memory Platform
#
# Three separate kinds of memory, deliberately not merged into one table:
#
#   Vault     (pai_vault_facts)  canonical *structured* student state — CGPA,
#                                budget, target intake. Authoritative, schema
#                                validated, provenance-carrying. NOT vector
#                                memory; it is queried by structured filter.
#   Semantic  (pai_memories)     durable preferences/goals/constraints learned
#                                over time — "prefers research-focused unis".
#   Episodic  (pai_episodes)     things that *happened* — "removed University X
#                                because tuition exceeded budget".
#
# Nothing an LLM says lands in these tables directly. Extraction writes
# `pai_memory_candidates`; a deterministic reconciler promotes candidates into
# the three tables above. See app/memory/ for the services.
# ---------------------------------------------------------------------------


class VaultFieldDefinition(Base):
    """Schema for ONE Vault field, as data rather than as Python branches.

    The whole point of this table is that adding `tests.pte.score` is an INSERT,
    not a code change: reconciliation and retrieval read `validation_schema`,
    `cardinality` and `conflict_policy` from here instead of branching on the
    field name. There is deliberately no `if key == "cgpa"` anywhere.

    Rows are versioned rather than edited in place so a fact can always be
    re-validated against the definition that was in force when it was accepted.
    """
    __tablename__ = "pai_vault_field_definitions"

    id = Column(Text, primary_key=True, default=_uuid)
    # Dotted path, e.g. "education.cgpa", "tests.ielts.score".
    key = Column(Text, nullable=False)
    category = Column(Text, nullable=False)              # education | tests | finance | preferences | career | ...
    data_type = Column(Text, nullable=False)             # string | number | integer | boolean | object | array
    # JSON Schema fragment validated against a proposed value. Deterministic,
    # inspectable, and editable without a deploy.
    validation_schema = Column(JSONB, nullable=True)
    # "single": one active fact (a CGPA). "multi": a set (preferred countries).
    cardinality = Column(Text, nullable=False, default="single", server_default=text("'single'"))
    # How the reconciler resolves a new value against an existing active one:
    #   latest_wins        — supersede (most profile facts)
    #   highest_confidence — keep the better-evidenced value
    #   manual_review      — never auto-apply; park for a human/explicit command
    conflict_policy = Column(Text, nullable=False, default="latest_wins", server_default=text("'latest_wins'"))
    # normal | sensitive — sensitive fields are withheld from low-trust callers
    # and from prompt context unless explicitly requested.
    sensitivity = Column(Text, nullable=False, default="normal", server_default=text("'normal'"))
    # Whether this field should be offered to text/hybrid retrieval later.
    searchable = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    context_tags = Column(JSONB, nullable=True)
    required_for = Column(JSONB, nullable=True)
    profile_priority = Column(Integer, nullable=False, default=50, server_default=text("50"))
    extractable_from = Column(JSONB, nullable=True)
    verification_policy = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        # One *active* definition per key; superseded versions stay for audit.
        Index("uq_vault_field_key_version", "key", "version", unique=True),
        Index("idx_vault_field_enabled", "enabled"),
    )


class ProfileRequirement(Base):
    """Versioned rule used to decide whether counseling may be personalized.

    Student values never live here.  A row only points at canonical Vault or
    typed-record data and supplies the neutral question to ask when it is
    missing.
    """
    __tablename__ = "pai_profile_requirements"

    id = Column(Text, primary_key=True, default=_uuid)
    key = Column(Text, nullable=False)
    tier = Column(Text, nullable=False)  # critical | important | enrichment
    source_type = Column(Text, nullable=False)  # vault_fact | record_presence | record_field | journey_gap
    source_key = Column(Text, nullable=False)
    source_path = Column(Text, nullable=True)
    selector = Column(Text, nullable=False, default="any", server_default=text("'any'"))
    applicability = Column(JSONB, nullable=True)
    question = Column(Text, nullable=False)
    priority = Column(Integer, nullable=False, default=50, server_default=text("50"))
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint(
            "tier IN ('critical', 'important', 'enrichment')",
            name="ck_profile_requirement_tier",
        ),
        CheckConstraint(
            "source_type IN ('vault_fact', 'record_presence', 'record_field', 'journey_gap')",
            name="ck_profile_requirement_source_type",
        ),
        CheckConstraint(
            "selector IN ('any', 'current_or_highest')",
            name="ck_profile_requirement_selector",
        ),
        CheckConstraint("priority >= 0", name="ck_profile_requirement_priority"),
        CheckConstraint("version > 0", name="ck_profile_requirement_version"),
        Index("uq_profile_requirement_key_version", "key", "version", unique=True),
        Index("idx_profile_requirements_enabled", "enabled"),
    )


class VaultFact(Base):
    """One canonical structured fact about the student, with provenance.

    History rather than overwrite: superseding a fact sets `status='superseded'`
    and `valid_until`, and inserts a new row. "What was their CGPA in March and
    who told us?" stays answerable, which matters when an agent acts on a fact
    and the student later disputes it.

    Scoped by workspace_id, matching every other table here (v2.0 is
    one-student-one-workspace). `subject_user_id` is reserved for a future
    multi-student workspace and is unused today.
    """
    __tablename__ = "pai_vault_facts"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)        # reserved; NULL = the workspace's student
    field_key = Column(Text, nullable=False)             # -> pai_vault_field_definitions.key
    field_version = Column(Integer, nullable=True)       # definition version this was validated against
    value = Column(JSONB, nullable=False)                # always wrapped: {"value": ...}
    confidence = Column(Float, nullable=False, default=1.0, server_default=text("1.0"))
    # user_explicit | document | conversation | agent | system.
    # user_explicit outranks inference in the reconciler.
    source_type = Column(Text, nullable=False)
    claim_origin = Column(Text, nullable=True)
    capture_method = Column(Text, nullable=True)
    source_event_id = Column(Text, nullable=True)        # events.id that evidences this
    evidence = Column(JSONB, nullable=True)              # {"quote": "...", "file_id": "..."}
    valid_from = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    valid_until = Column(DateTime(timezone=True), nullable=True)
    # active | superseded | retracted (retracted = user said "that's wrong")
    status = Column(Text, nullable=False, default="active", server_default=text("'active'"))
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_vault_facts_workspace", "workspace_id"),
        Index(
            "uq_vault_facts_ws_key_active", "workspace_id", "field_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        # The hot read: "active facts for this workspace", and the single-
        # cardinality conflict lookup by key.
        Index("idx_vault_facts_ws_status_key", "workspace_id", "status", "field_key"),
    )


class _StudentRecord:
    """Shared audit columns for repeatable, workspace-scoped student records."""

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)
    source_type = Column(Text, nullable=False)
    claim_origin = Column(Text, nullable=False)
    capture_method = Column(Text, nullable=False)
    verification_status = Column(Text, nullable=False, default="self_reported", server_default=text("'self_reported'"))
    evidence = Column(JSONB, nullable=True)
    status = Column(Text, nullable=False, default="active", server_default=text("'active'"))
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))


class EducationRecord(_StudentRecord, Base):
    __tablename__ = "pai_education_records"
    institution_name = Column(Text, nullable=True)
    qualification_name = Column(Text, nullable=False)
    canonical_level = Column(Text, nullable=True)
    field_of_study = Column(Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    graduation_year = Column(Integer, nullable=True)
    academic_status = Column(Text, nullable=True)
    result = Column(JSONB, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_education_ws", "workspace_id", "status"),)


class CourseRecord(_StudentRecord, Base):
    __tablename__ = "pai_course_records"
    education_id = Column(Text, ForeignKey("pai_education_records.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=True)
    grade = Column(Text, nullable=True)
    score = Column(JSONB, nullable=True)
    credits = Column(Float, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_course_education", "education_id"),)


class TestAttempt(_StudentRecord, Base):
    __tablename__ = "pai_test_attempts"
    test_type = Column(Text, nullable=False)
    original_name = Column(Text, nullable=True)
    attempt_number = Column(Integer, nullable=True)
    test_date = Column(Text, nullable=True)
    expiry_date = Column(Text, nullable=True)
    overall_score = Column(Text, nullable=True)
    section_scores = Column(JSONB, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_tests_ws", "workspace_id", "status", "test_type"),)


class WorkExperience(_StudentRecord, Base):
    __tablename__ = "pai_work_experiences"
    organization = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    experience_type = Column(Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_work_ws", "workspace_id", "status"),)


class StudentProject(_StudentRecord, Base):
    __tablename__ = "pai_student_projects"
    name = Column(Text, nullable=False)
    role = Column(Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_projects_ws", "workspace_id", "status"),)


class StudentGoal(_StudentRecord, Base):
    __tablename__ = "pai_student_goals"
    goal_type = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    commitment = Column(Text, nullable=True)
    target_date = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_goals_ws", "workspace_id", "status"),)


class StudentSkill(_StudentRecord, Base):
    __tablename__ = "pai_student_skills"
    name = Column(Text, nullable=False)
    proficiency = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_skills_ws", "workspace_id", "status"),)


class StudentCertification(_StudentRecord, Base):
    __tablename__ = "pai_student_certifications"
    name = Column(Text, nullable=False)
    issuer = Column(Text, nullable=True)
    issued_on = Column(Text, nullable=True)
    expires_on = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_certifications_ws", "workspace_id", "status"),)


class StudentApplication(_StudentRecord, Base):
    __tablename__ = "pai_student_applications"
    institution_name = Column(Text, nullable=False)
    program_name = Column(Text, nullable=True)
    intake = Column(Text, nullable=True)
    application_status = Column(Text, nullable=True)
    deadline = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_applications_ws", "workspace_id", "status"),)


class StudentDocument(_StudentRecord, Base):
    __tablename__ = "pai_student_documents"
    file_id = Column(Text, nullable=False)
    document_type = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_documents_ws", "workspace_id", "status"),)


class LanguageProficiency(_StudentRecord, Base):
    __tablename__ = "pai_language_proficiencies"
    language = Column(Text, nullable=False)
    proficiency = Column(Text, nullable=True)
    evidence_type = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_languages_ws", "workspace_id", "status"),)


class ResearchRecord(_StudentRecord, Base):
    __tablename__ = "pai_research_records"
    title = Column(Text, nullable=False)
    organization = Column(Text, nullable=True)
    role = Column(Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_research_ws", "workspace_id", "status"),)


class AchievementRecord(_StudentRecord, Base):
    __tablename__ = "pai_achievement_records"
    title = Column(Text, nullable=False)
    achievement_type = Column(Text, nullable=True)
    issuer = Column(Text, nullable=True)
    achieved_on = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_achievements_ws", "workspace_id", "status"),)


class FinancialSponsor(_StudentRecord, Base):
    __tablename__ = "pai_financial_sponsors"
    sponsor_type = Column(Text, nullable=False)
    name = Column(Text, nullable=True)
    commitment_status = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_sponsors_ws", "workspace_id", "status"),)


class ScholarshipApplication(_StudentRecord, Base):
    __tablename__ = "pai_scholarship_applications"
    scholarship_name = Column(Text, nullable=False)
    provider = Column(Text, nullable=True)
    application_status = Column(Text, nullable=True)
    deadline = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_scholarships_ws", "workspace_id", "status"),)


class VisaRecord(_StudentRecord, Base):
    __tablename__ = "pai_visa_records"
    country = Column(Text, nullable=False)
    visa_type = Column(Text, nullable=True)
    application_status = Column(Text, nullable=True)
    expiry_date = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    __table_args__ = (Index("idx_pai_visas_ws", "workspace_id", "status"),)


class ProfileIssue(Base):
    __tablename__ = "pai_profile_issues"
    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)
    issue_type = Column(Text, nullable=False)
    severity = Column(Text, nullable=False, default="warning", server_default=text("'warning'"))
    affected_type = Column(Text, nullable=True)
    affected_id = Column(Text, nullable=True)
    summary = Column(Text, nullable=False)
    clarification_question = Column(Text, nullable=True)
    candidate_id = Column(Text, ForeignKey("pai_memory_candidates.id", ondelete="SET NULL"), nullable=True)
    evidence = Column(JSONB, nullable=True)
    status = Column(Text, nullable=False, default="open", server_default=text("'open'"))
    resolution = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("idx_pai_issues_ws", "workspace_id", "status"),
        Index("idx_pai_issues_candidate", "candidate_id"),
    )


class StudentRecordRevision(Base):
    """Audit trail for changes to a stable, repeatable student record."""
    __tablename__ = "pai_student_record_revisions"
    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    record_type = Column(Text, nullable=False)
    record_id = Column(Text, nullable=False)
    before = Column(JSONB, nullable=True)
    after = Column(JSONB, nullable=False)
    source_type = Column(Text, nullable=False)
    claim_origin = Column(Text, nullable=False)
    capture_method = Column(Text, nullable=False)
    evidence = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    __table_args__ = (Index("idx_pai_record_revision_ws", "workspace_id", "record_type", "record_id"),)


class PaiMemory(Base):
    """Semantic memory — a durable learned statement about the student.

    Canonical text lives here in PostgreSQL. Embeddings live in a retrieval
    index keyed by `id` (see app/memory/index.py); deliberately no vector
    column and no provider-specific field on this model, so swapping pgvector
    for Qdrant never touches the business schema.
    """
    __tablename__ = "pai_memories"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)
    # preference | goal | constraint | interest | context
    memory_type = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    entities = Column(JSONB, nullable=True)              # {"countries": ["DE"], "universities": [...]}
    importance = Column(Float, nullable=False, default=0.5, server_default=text("0.5"))
    confidence = Column(Float, nullable=False, default=1.0, server_default=text("1.0"))
    source_type = Column(Text, nullable=True)
    source_event_ids = Column(JSONB, nullable=True)
    meta = Column("metadata", JSONB, nullable=True)      # attr renamed: `metadata` is reserved by SQLAlchemy
    valid_from = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    valid_until = Column(DateTime(timezone=True), nullable=True)
    # active | superseded | forgotten  ("forget Canada" -> forgotten, not deleted)
    # Exact-normalized dedupe key (app/memory/dedupe.py). Indexed so dedupe is
    # a lookup, not a scan. NOT semantic similarity — that is the vector index.
    fingerprint = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="active", server_default=text("'active'"))
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_pai_memories_workspace", "workspace_id"),
        Index("idx_pai_memories_ws_status_type", "workspace_id", "status", "memory_type"),
        Index(
            "idx_pai_memories_ws_status_fingerprint",
            "workspace_id", "status", "fingerprint",
        ),
        # The actual no-duplicates invariant. The application's fingerprint
        # lookup is a fast path; this is what makes two concurrent workers
        # safe. Partial so forgotten rows may share a fingerprint and a NULL
        # fingerprint never collides.
        Index(
            "uq_pai_memories_ws_fingerprint_active", "workspace_id", "fingerprint",
            unique=True,
            postgresql_where=text("status = 'active' AND fingerprint IS NOT NULL"),
            sqlite_where=text("status = 'active' AND fingerprint IS NOT NULL"),
        ),
    )


class PaiEpisode(Base):
    """Episodic memory — something that happened, with a time it happened at.

    Separate from semantic memory because the useful query differs: episodes
    are retrieved by recency and event type ("what changed lately?"), whereas
    semantic memories are retrieved by similarity to the current question.
    """
    __tablename__ = "pai_episodes"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)
    # e.g. shortlist_removed | document_uploaded | deadline_missed | decision_made
    event_type = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    entities = Column(JSONB, nullable=True)
    importance = Column(Float, nullable=False, default=0.5, server_default=text("0.5"))
    occurred_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    source_event_ids = Column(JSONB, nullable=True)
    meta = Column("metadata", JSONB, nullable=True)
    # Exact-normalized dedupe key — see PaiMemory.fingerprint.
    fingerprint = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="active", server_default=text("'active'"))
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_pai_episodes_workspace", "workspace_id"),
        Index("idx_pai_episodes_ws_status_time", "workspace_id", "status", "occurred_at"),
        Index(
            "idx_pai_episodes_ws_status_fingerprint",
            "workspace_id", "status", "fingerprint",
        ),
        Index(
            "uq_pai_episodes_ws_fingerprint_active", "workspace_id", "fingerprint",
            unique=True,
            postgresql_where=text("status = 'active' AND fingerprint IS NOT NULL"),
            sqlite_where=text("status = 'active' AND fingerprint IS NOT NULL"),
        ),
    )


class MemoryCandidate(Base):
    """A *proposal* to change memory. The quarantine between LLMs and truth.

    Extraction (an LLM) writes rows here and nothing else. The reconciler reads
    them and decides what, if anything, becomes canonical. This is the seam
    that makes "an LLM hallucinated a CGPA of 9.9" a rejected candidate row
    rather than a corrupted Vault.
    """
    __tablename__ = "pai_memory_candidates"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    subject_user_id = Column(Text, nullable=True)
    candidate_type = Column(Text, nullable=False)        # vault_fact | semantic_memory | episode
    operation = Column(Text, nullable=False)             # upsert | retract | forget
    key = Column(Text, nullable=True)                    # field_key for vault_fact
    proposed_value = Column(JSONB, nullable=True)        # {"value": ...} for vault_fact
    content = Column(Text, nullable=True)                # text for semantic/episode
    entities = Column(JSONB, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5, server_default=text("0.5"))
    source_type = Column(Text, nullable=False, default="conversation", server_default=text("'conversation'"))
    source_event_ids = Column(JSONB, nullable=True)
    evidence = Column(JSONB, nullable=True)
    # pending | needs_review | accepted | rejected | superseded
    status = Column(Text, nullable=False, default="pending", server_default=text("'pending'"))
    rejection_reason = Column(Text, nullable=True)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)
    result_id = Column(Text, nullable=True)              # id of the row this produced, when accepted
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_memory_candidates_workspace", "workspace_id"),
        Index("idx_memory_candidates_ws_status", "workspace_id", "status"),
    )


class DocumentArtifact(Base):
    """DERIVED parse state for one uploaded PDF/DOCX. Never student truth.

    Two rules define this table:

    1. **Rebuildable.** Everything here can be recreated from the raw file in
       workspace storage. Losing the row costs a reprocess, never a fact. The
       canonical claims a document produced live in Vault/typed records with
       their own provenance, and they outlive this row on purpose — an
       evidence file going away does not retract what was learned from it.

    2. **One row per file.** `file_id` is unique, so a retried parse updates
       in place instead of accumulating near-duplicate extractions. That is
       what makes the whole document pipeline idempotent.

    `content_sha256` is the idempotency anchor: re-uploading identical bytes,
    or retrying a job, resolves to the same derived state rather than a second
    extraction pass. `parser_version` sits beside it so a parser upgrade can
    invalidate and reprocess deliberately.

    The normalized parse output is stored as JSONB `content` — pages/sections
    with locators, which is what evidence quotes point at. It stays off
    FileRecord because FileRecord is hot metadata read by every listing, and a
    multi-page document body has no business being loaded to render a filename.
    """
    __tablename__ = "pai_document_artifacts"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    file_id = Column(Text, ForeignKey("files.id", ondelete="CASCADE"), nullable=False)

    # queued | processing | ready | partial | failed | unsupported
    #   partial — parsed, but something downstream (a page, OCR) did not land.
    #             Readable, explicitly incomplete; not a failure.
    status = Column(Text, nullable=False, default="queued", server_default=text("'queued'"))
    document_type = Column(Text, nullable=True)      # pdf | docx (detected)
    detected_content_type = Column(Text, nullable=True)
    content_sha256 = Column(Text, nullable=True)

    parser = Column(Text, nullable=True)             # pypdf | python-docx | ...
    parser_version = Column(Text, nullable=True)
    ocr_used = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    ocr_provider = Column(Text, nullable=True)

    page_count = Column(Integer, nullable=True)
    char_count = Column(Integer, nullable=True)
    #: {"pages": [{"locator": "p1", "text": "...", "tables": [...]}, ...]}
    content = Column(JSONB, nullable=True)

    # Document intelligence, filled by the extract stage.
    classification = Column(Text, nullable=True)     # transcript | cv_resume | ...
    classification_confidence = Column(Float, nullable=True)
    authority = Column(Text, nullable=True)          # institution_issued | student_authored | ...
    extractor_version = Column(Text, nullable=True)
    #: Counts only — never extracted student content.
    extraction_summary = Column(JSONB, nullable=True)

    error_code = Column(Text, nullable=True)         # safe, stable, client-visible
    error_message = Column(Text, nullable=True)      # safe summary; never raw model/PII text
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))

    __table_args__ = (
        # One derived row per file — the idempotency invariant.
        UniqueConstraint("file_id", name="uq_document_artifact_file"),
        Index("idx_document_artifacts_ws_status", "workspace_id", "status"),
        Index("idx_document_artifacts_ws_hash", "workspace_id", "content_sha256"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'partial', 'failed', 'unsupported')",
            name="ck_document_artifact_status",
        ),
    )


class BackgroundJob(Base):
    """Durable work queue. Deliberately generic — not a memory-only table.

    PAI Operator uses `asyncio.create_task()`, which loses work on restart.
    That is acceptable for a run whose status the user is watching; it is not
    acceptable for memory formation, where silent loss means the student's
    profile quietly drifts from what they told us.

    Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL so N
    workers never hand the same job out twice. PostgreSQL stays the source of
    truth — no broker.
    """
    __tablename__ = "background_jobs"

    id = Column(Text, primary_key=True, default=_uuid)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    job_type = Column(Text, nullable=False)              # e.g. memory.extract, memory.embed
    payload = Column(JSONB, nullable=True)
    # pending | running | succeeded | failed | cancelled
    status = Column(Text, nullable=False, default="pending", server_default=text("'pending'"))
    priority = Column(Integer, nullable=False, default=0, server_default=text("0"))
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts = Column(Integer, nullable=False, default=5, server_default=text("5"))
    # Visibility timestamp: a job is claimable only once NOW() >= available_at.
    # Retry backoff is just pushing this forward.
    available_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = Column(Text, nullable=True)              # worker id, for stale-lock reclaim
    last_error = Column(Text, nullable=True)
    # Unique when present -> enqueueing the same logical work twice is a no-op.
    idempotency_key = Column(Text, nullable=True)
    result = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("NOW()"))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The claim query's index: pending jobs that are due, best first.
        Index("idx_background_jobs_claim", "status", "available_at", "priority"),
        Index("idx_background_jobs_workspace", "workspace_id"),
        UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency"),
    )
