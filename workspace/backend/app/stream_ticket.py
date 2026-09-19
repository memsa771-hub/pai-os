# -*- coding: utf-8 -*-
"""Short-lived, read-only URL credentials for the browser.

Two browser features cannot send an `Authorization` header: `EventSource`
(SSE) and any URL handed to `<img src>` / `<a href>` for a file download.
Both used to carry `?token=<workspace.password_hash>` — the workspace MACHINE
token, the same credential PAI's agents authenticate with. That put a
long-lived, full-write, workspace-wide secret into a JS-readable cookie, into
browser history, and into every proxy and referrer log on the path.

A ticket replaces it, and is deliberately weaker in every dimension that
matters:

  * it expires in minutes, not never;
  * it is bound to one workspace and one user id;
  * it is accepted on exactly two GET routes (SSE, file download) and nowhere
    else, so it can never write;
  * it cannot be turned back into the machine token.

There is no ticket table and no Redis dependency: the ticket is signed with
the workspace's own `password_hash` as the HMAC key. That secret already
exists, is unique per workspace, is known only to the server now, and never
appears in the ticket. It also gives revocation for free — rotating a
workspace's token invalidates every outstanding ticket for it.
"""

import base64
import hashlib
import hmac
import time
from typing import Iterable, Optional

# Long enough that a student reading one page doesn't get a dead image, short
# enough that a leaked URL is worthless by the time it reaches a log reader.
# Streams outlive this: the SSE connection is authorized once, at connect.
TICKET_TTL_SECONDS = 15 * 60

_SIGNING_CONTEXT = b"pai.stream-ticket.v2"
EVENTS_SCOPE = "events"
FILES_SCOPE = "files"
_ALLOWED_SCOPES = frozenset({EVENTS_SCOPE, FILES_SCOPE})


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: bytes) -> str:
    return _b64(hmac.new(secret.encode(), _SIGNING_CONTEXT + payload, hashlib.sha256).digest())


def mint(
    workspace,
    user_id: str,
    ttl_seconds: int = TICKET_TTL_SECONDS,
    scopes: Iterable[str] = (EVENTS_SCOPE, FILES_SCOPE),
) -> Optional[str]:
    """Mint a bounded, signed ticket for one user, workspace, and route set."""
    if not workspace.password_hash or not user_id or str(workspace.owner_user_id) != user_id:
        return None
    requested_scopes = sorted(set(scopes) & _ALLOWED_SCOPES)
    if not requested_scopes:
        return None
    ttl_seconds = min(max(int(ttl_seconds), 1), TICKET_TTL_SECONDS)
    expires = int(time.time()) + ttl_seconds
    payload = f"{workspace.id}:{user_id}:{expires}:{','.join(requested_scopes)}".encode()
    return f"{_b64(payload)}.{_sign(workspace.password_hash, payload)}"


def verify(workspace, ticket: Optional[str], required_scope: str) -> bool:
    """True for a live ticket bound to this workspace and route scope.

    The workspace is the caller's, resolved from the request before we get
    here, so a valid ticket for workspace A presented against workspace B
    fails on the signature: B's secret is a different HMAC key.
    """
    if not ticket or not workspace.password_hash:
        return False
    encoded, _, signature = ticket.partition(".")
    if not signature:
        return False
    try:
        payload = _unb64(encoded)
        workspace_id, user_id, expires, scopes_text = payload.decode().split(":", 3)
        expires_at = int(expires)
        scopes = set(scopes_text.split(","))
    except Exception:
        return False

    if not hmac.compare_digest(signature, _sign(workspace.password_hash, payload)):
        return False
    if workspace_id != str(workspace.id):
        return False
    if (not user_id or str(workspace.owner_user_id) != user_id
            or required_scope not in _ALLOWED_SCOPES or required_scope not in scopes):
        return False
    return time.time() < expires_at
