# -*- coding: utf-8 -*-
"""Who is speaking? The one place public event ingress decides that.

The client says WHAT happened. The server decides WHO said it.

`POST /v1/events` used to take `source` straight from the request body and
persist and route it as the event's identity, so any caller holding a
workspace token could post as `openagents:pai`, `system:workspace`, or another
student. Identity is now derived here, from credentials only, and the body's
`source` is ignored.

Two kinds of actor can reach public ingress:

  HUMAN   a verified bearer whose User owns this workspace
          -> "human:<user.id>"

          The id, not the email: it is the stable primary key, it cannot be
          spoofed by changing a display name, and `workspaces.owner_user_id`
          is already the tenant boundary (see app/access.py). Any display name
          or address in the payload stays presentation metadata — never
          identity, never authorization.

  AGENT   the workspace machine token PLUS a live session id
          -> "openagents:<member.agent_name>"

          The agent name is looked up FROM the session, never read from the
          request. The workspace token is shared by every agent in the
          workspace, so it proves machine access but says nothing about which
          agent is speaking; `WorkspaceMember.session_id` is per-join and
          rotates, so it is the only credential that identifies one. A caller
          cannot name itself, which is what makes impersonation structurally
          impossible rather than merely checked.

Anything else is not an actor and cannot post. In particular there is no path
here that yields a `system:*` source: those belong to trusted server code,
which builds events directly (see `emit_internal_event`) instead of coming
through the public door.
"""

import logging
import secrets as _secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import extract_bearer
from app.firebase_auth import verify_identity_claims
from app.models import User, Workspace, WorkspaceMember

logger = logging.getLogger(__name__)

HUMAN_PREFIX = "human:"
AGENT_PREFIX = "openagents:"


@dataclass(frozen=True)
class Actor:
    """An authenticated speaker, and the source string the server will use."""

    source: str
    kind: str                       # "human" | "agent"
    user_id: Optional[str] = None
    agent_name: Optional[str] = None
    session_id: Optional[str] = None


def reserved_agent_names() -> frozenset:
    """Agent names public callers may never assume.

    PAI Counselor and PAI Operator are server-managed identities. They are
    provisioned by trusted code and speak through it; nobody joins as them.
    """
    from app.services.operator import PAI_OPERATOR_AGENT_NAME
    from app.services.pai import PAI_AGENT_NAME

    return frozenset({PAI_AGENT_NAME.casefold(), PAI_OPERATOR_AGENT_NAME.casefold()})


def resolve_human(db: Session, workspace: Workspace, authorization: Optional[str]) -> Optional[Actor]:
    """The owner of this workspace, if that is who is calling."""
    bearer = extract_bearer(authorization)
    if not bearer:
        return None
    if workspace.owner_user_id is None:
        return None

    claims = verify_identity_claims(bearer)
    if not claims:
        return None
    email = (claims.get("email") or "").strip().lower()
    if not email:
        return None

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or str(user.id) != str(workspace.owner_user_id):
        return None

    return Actor(source=f"{HUMAN_PREFIX}{user.id}", kind="human", user_id=str(user.id))


def resolve_agent(
    db: Session, workspace: Workspace, token: Optional[str], session_id: Optional[str],
) -> Optional[Actor]:
    """The agent that owns `session_id`, if the machine token is also valid.

    Both halves are required. The token alone is the workspace's shared machine
    credential and identifies no one; the session alone is not proof of access.
    The session is scoped to this workspace in the query, so a session issued by
    another workspace resolves to nothing here rather than to its own agent.
    """
    if not token or not workspace.password_hash or token != workspace.password_hash:
        return None
    if not session_id:
        return None

    member = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.session_id == session_id,
        )
    ).scalar_one_or_none()
    if member is None:
        return None

    return Actor(
        source=f"{AGENT_PREFIX}{member.agent_name}",
        kind="agent",
        agent_name=member.agent_name,
        session_id=session_id,
    )


def resolve_actor(
    db: Session,
    workspace: Workspace,
    *,
    token: Optional[str] = None,
    authorization: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Actor]:
    """The authoritative actor for a public request, or None to reject.

    Human first: a request carrying both a valid bearer and a workspace token
    is a person using a client that also knows the token, and attributing that
    to an agent would be wrong.
    """
    human = resolve_human(db, workspace, authorization)
    if human is not None:
        return human
    return resolve_agent(db, workspace, token, session_id)


# ---------------------------------------------------------------------------
# The in-process trusted principal
# ---------------------------------------------------------------------------

# Regenerated every boot and never written down, logged or sent over a socket.
# `pai.WorkspaceApi` talks to this same FastAPI app through
# `httpx.ASGITransport`, so PAI's tools are literally in this process and can
# read it; nothing outside the process can. That is what lets a tool carry its
# ToolContext principal across the in-process HTTP boundary without the
# workspace token — which is shared and identifies nobody — becoming an
# identity claim again.
_INTERNAL_SECRET = _secrets.token_urlsafe(32)

INTERNAL_ACTOR_HEADER = "X-Internal-Actor"


def mint_internal_actor(source: str) -> str:
    """Header value asserting a server-side principal. In-process callers only."""
    return f"{_INTERNAL_SECRET}.{source}"


def resolve_internal_actor(header_value: Optional[str]) -> Optional[Actor]:
    """The principal a trusted in-process caller asserted, if the secret matches.

    A forged header fails `compare_digest` and resolves to nothing, so an
    external caller gains exactly what they had before: no identity.
    """
    if not header_value:
        return None
    presented, _, source = header_value.partition(".")
    if not source or not _secrets.compare_digest(presented, _INTERNAL_SECRET):
        return None
    if source.startswith(AGENT_PREFIX):
        return Actor(source=source, kind="agent",
                     agent_name=source[len(AGENT_PREFIX):])
    return Actor(source=source, kind="internal")


def resolve_request_actor(
    db: Session,
    workspace: Workspace,
    *,
    token: Optional[str] = None,
    authorization: Optional[str] = None,
    session_id: Optional[str] = None,
    internal_actor: Optional[str] = None,
) -> Optional[Actor]:
    """The actor for any mutating request, public or internal.

    One resolution order for every router, so `source` never has to be accepted
    from a request body again:

      1. a trusted in-process principal (PAI's own tools)
      2. the workspace owner, by verified bearer
      3. an agent, by machine token + live session
    """
    internal = resolve_internal_actor(internal_actor)
    if internal is not None:
        return internal
    return resolve_actor(
        db, workspace, token=token, authorization=authorization, session_id=session_id,
    )


def request_actor_source(db, workspace, token, authorization, internal_actor,
                         session_id=None) -> Optional[str]:
    """Who is acting on a mutating request, decided by the server.

    The router-facing shape of `resolve_request_actor`: positional in the
    order a handler already has these values, returning just the source string
    (or None, which the caller turns into a 401).

    `source` used to be a request-body field, typically defaulting to
    "human:user", so any authenticated caller could attribute a write to PAI,
    to another agent, or to `system:*`. Every router that writes an attributed
    row goes through here instead.
    """
    actor = resolve_request_actor(
        db, workspace, token=token, authorization=authorization,
        session_id=session_id, internal_actor=internal_actor,
    )
    return actor.source if actor else None


def session_id_from(body_metadata: Optional[dict], header_value: Optional[str]) -> Optional[str]:
    """Where a session id may travel.

    `X-Session-Id` is the explicit way. `metadata.session_id` is what the
    shipped agent-connector already sends on every event, and is accepted so
    existing agents keep working. Neither is trusted as identity: a session id
    is a credential to be looked up, not a name to be believed.
    """
    if header_value:
        return header_value.strip() or None
    if isinstance(body_metadata, dict):
        value = body_metadata.get("session_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# Trusted internal events
# ---------------------------------------------------------------------------

async def emit_internal_event(
    db: Session,
    workspace: Workspace,
    *,
    type: str,
    source: str,
    target: str,
    payload: Optional[dict] = None,
    metadata: Optional[dict] = None,
    visibility: str = "channel",
):
    """Run a server-authored event through the pipeline, identity included.

    For code that IS the server — PAI Counselor's replies, Operator's result
    posting, the workflow engine, timers and routines. Those legitimately speak
    as `openagents:pai` or `system:*`, and they are trusted because of where
    they run, not because of anything a request said.

    Deliberately not reachable over HTTP. `POST /v1/events` derives identity
    from credentials and can never produce these sources, which is the whole
    point: the trusted path is a function call, not a header a caller could
    forge. Anything that can call this can already write to the database.
    """
    from app.pipeline_factory import pipeline
    from openagents.core.onm_events import Event
    from openagents.core.onm_mods import PipelineContext

    event = Event(
        type=type,
        source=source,
        target=target,
        payload=payload or {},
        metadata=metadata or {},
        visibility=visibility,
        network=str(workspace.id),
    )
    context = PipelineContext(
        network_id=str(workspace.id),
        agent_address=source,
        db=db,
        workspace=workspace,
        # The workspace's own token: this call originates inside the server, so
        # it authenticates as the workspace rather than borrowing a caller's
        # credentials.
        token=workspace.password_hash,
    )
    return await pipeline.process(event, context)
