# -*- coding: utf-8 -*-
"""
Account-level endpoints for the signed-in end user (Supabase or Apple identity).

DELETE /v1/account    Permanently delete the calling user's account data.

This exists to satisfy App Store Review Guideline 5.1.1(v), which requires apps
that support account creation to let users initiate account deletion from inside
the app. Auth is the user's identity bearer token (Authorization: Bearer <id>)
— NOT a workspace token — because deletion spans every workspace the user
touched, so it can't be scoped to a single workspace's token.

Scope of deletion: the user is identified only by email (the app has no
app-managed credential; identity is delegated to Google / Apple / Supabase).
Placement AI v2.0: the user's own personal workspace (`owner_user_id`) is
private to them, so it is soft-deleted along with every other email-keyed
row — collaborator memberships, channel human memberships, and registered
device push tokens — across any workspace they touched.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import (
    get_or_create_owned_workspace,
    resolve_current_user,
    resolve_owned_workspace,
)
from app.database import get_db
from app.firebase_auth import verify_identity_token
from app.models import (
    ChannelHumanMember,
    DeviceToken,
    EventRecord,
    FileRecord,
    User,
    Workspace,
)
from app.response import ResponseCode, json_response, success_response
from app.storage import get_file_store
from app.stream_ticket import TICKET_TTL_SECONDS
from app.stream_ticket import mint as mint_stream_ticket
from app.routers.network import _extract_bearer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Account"])


def _authed_email(authorization: Optional[str]) -> Optional[str]:
    """Resolve the calling user's normalized email from the identity bearer,
    or None if absent/invalid."""
    bearer = _extract_bearer(authorization)
    if not bearer:
        return None
    email = verify_identity_token(bearer)
    return email.strip().lower() if email else None


def _canonical_workspace_row(ws: Workspace, user: Optional[User] = None) -> dict:
    """The workspace as the browser sees it. Deliberately WITHOUT its token.

    This used to return `ws.password_hash` — the workspace machine token, the
    credential PAI's agents write with — because EventSource cannot send a
    header and needed something in the URL. The browser then kept it in a
    30-day JS-readable cookie. A read-only, minutes-long `streamTicket` now
    covers that need instead (see app/stream_ticket.py); the machine token
    never leaves the server.
    """
    row = {
        "workspaceId": str(ws.id),
        "name": ws.name,
        "slug": ws.slug,
        "lastActivityAt": ws.last_activity_at.isoformat() if ws.last_activity_at else None,
    }
    if user is not None:
        row["streamTicket"] = mint_stream_ticket(ws, str(user.id))
        row["streamTicketTtl"] = TICKET_TTL_SECONDS
    return row


@router.get("/account/workspace")
def get_account_workspace(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """The signed-in student's one canonical personal workspace.

    Placement AI v2.0: a user account has exactly one active workspace,
    identified by `workspaces.owner_user_id`. Creates it — with PAI Counselor
    and the canonical welcome conversation — the first time this is called for
    a given user; every call after that returns the same workspace.
    Concurrency-safe (see `app.access.get_or_create_owned_workspace`): two
    simultaneous first logins (two tabs, web + desktop) can never create two
    workspaces for the same user.
    """
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    workspace = get_or_create_owned_workspace(db, user)
    db.commit()
    db.refresh(workspace)

    return success_response(_canonical_workspace_row(workspace, user))


@router.post("/account/stream-ticket")
def refresh_stream_ticket(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """A fresh read-only ticket for SSE and file URLs.

    Tickets expire in minutes, so the browser re-mints as it goes. Only the
    verified owner of the workspace can mint one, and the ticket it gets back
    grants strictly less than the bearer it was minted with.
    """
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    workspace = resolve_owned_workspace(db, user)
    db.commit()
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "No workspace")

    return success_response({
        "streamTicket": mint_stream_ticket(workspace, str(user.id)),
        "streamTicketTtl": TICKET_TTL_SECONDS,
    })


@router.get("/account/workspaces")
def list_account_workspaces(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """DEPRECATED (v1.0 Membership Home) — use GET /v1/account/workspace.

    A Placement AI account now has exactly one workspace; this always returns
    a single-element array wrapping it (see `get_account_workspace`), kept for
    any client not yet updated to the singular endpoint. New code should not
    call this.
    """
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    workspace = get_or_create_owned_workspace(db, user)
    db.commit()
    db.refresh(workspace)

    return success_response([{**_canonical_workspace_row(workspace, user), "role": "owner"}])


# ---------------------------------------------------------------------------
# Profile — the signed-in user's cross-workspace identity card
# ---------------------------------------------------------------------------

# Generous cap for a data:image/... avatar: a 256px JPEG is ~10-40KB; base64
# adds ~33%. Anything bigger means the client skipped its downscaling step.
MAX_AVATAR_URL_LENGTH = 200_000


class ProfileUpdateRequest(BaseModel):
    # Accept both the wire name ("welcomeSeen") and the field name.
    model_config = ConfigDict(populate_by_name=True)

    display_name: Optional[str] = Field(default=None, max_length=120)
    # "" clears the avatar; None leaves it untouched.
    avatar_url: Optional[str] = None
    # First-run welcome dismissed on this account; None leaves it untouched.
    welcome_seen: Optional[bool] = Field(default=None, alias="welcomeSeen")


def _profile_row(user) -> dict:
    return {
        "email": user.email,
        "displayName": user.display_name,
        "avatarUrl": user.avatar_url,
        "welcomeSeen": bool(user.welcome_seen),
    }


@router.get("/account/profile")
def get_profile(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """The signed-in user's profile (name + avatar), shared across workspaces."""
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")
    db.commit()  # persist the lazily created/refreshed User row
    return success_response(_profile_row(user))


@router.patch("/account/profile")
def update_profile(
    body: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Update display name and/or avatar. Omitted fields are left untouched;
    an empty-string avatar_url clears the picture."""
    user = resolve_current_user(db, authorization)
    if not user:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            return json_response(ResponseCode.BAD_REQUEST, "Display name cannot be empty")
        user.display_name = name

    if body.avatar_url is not None:
        avatar = body.avatar_url.strip()
        if not avatar:
            user.avatar_url = None
        else:
            if not (avatar.startswith("https://") or avatar.startswith("data:image/")):
                return json_response(
                    ResponseCode.BAD_REQUEST,
                    "Avatar must be an https:// URL or a data:image/... URL",
                )
            if len(avatar) > MAX_AVATAR_URL_LENGTH:
                return json_response(ResponseCode.BAD_REQUEST, "Avatar image is too large")
            user.avatar_url = avatar

    if body.welcome_seen is not None:
        user.welcome_seen = body.welcome_seen

    db.commit()
    return success_response(_profile_row(user))


def _purge_workspace_vectors(workspace_id: str) -> None:
    """Drop this workspace's points from the memory index.

    Best effort, and deliberately so: the student's erasure must not be
    blocked by the availability of a search index. If it fails the points are
    already unreachable — every query filters by workspace_id and the
    workspace no longer exists — but they are still bytes on disk, so the
    failure is logged loudly enough to clean up by hand.
    """
    try:
        import asyncio

        from app.memory.index import get_memory_index

        index = get_memory_index()
        if index is None or not hasattr(index, "drop_workspace"):
            return
        asyncio.run(index.drop_workspace(workspace_id))
    except Exception:
        logger.warning(
            "account: could not purge memory vectors for workspace %s — "
            "orphaned points remain in the index",
            workspace_id, exc_info=True,
        )


def _erase_workspace(db: Session, workspace: Workspace) -> dict:
    """Destroy one workspace and everything in it. Not recoverable.

    Order matters. File BYTES have to be removed before the rows naming them
    are gone, and `events` has no foreign key to `workspaces` (only a plain
    `network_id` column), so it does not cascade and has to be deleted by
    hand. Everything else — memories, vault facts, episodes, knowledge,
    tasks, browser contexts, notifications, integrations, model credentials —
    hangs off `workspace_id` with `ondelete="CASCADE"`, so dropping the
    workspace row takes all of it.
    """
    workspace_id = str(workspace.id)

    store = get_file_store()
    records = db.execute(
        select(FileRecord).where(FileRecord.workspace_id == workspace_id)
    ).scalars().all()
    files_deleted, file_errors = 0, 0
    for record in records:
        try:
            store.delete(record.storage_key)
            files_deleted += 1
        except FileNotFoundError:
            files_deleted += 1          # already gone is the desired end state
        except Exception:
            file_errors += 1
            logger.warning(
                "account: could not delete stored file %s", record.storage_key,
                exc_info=True,
            )

    _purge_workspace_vectors(workspace_id)

    events_deleted = db.query(EventRecord).filter(
        EventRecord.network_id == workspace_id
    ).delete(synchronize_session=False)

    db.delete(workspace)               # CASCADE does the rest
    return {
        "workspaceId": workspace_id,
        "files": files_deleted,
        "fileErrors": file_errors,
        "events": events_deleted,
    }


@router.delete("/account")
def delete_account(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Permanently erase the calling user's account and everything in it.

    This used to only mark the workspace `status = "deleted"` and remove
    device tokens. Everything that actually matters survived: the student's
    memories and episodes, their vault facts (CGPA, test scores), every file
    they had uploaded — transcripts, passports — their whole chat history, and
    the index vectors built from all of it. "Delete my account" has to mean
    the data is gone, not hidden behind a status column.

    So it is now a hard delete, in one transaction: either every row goes or
    none does. The `users` row goes too, which frees the email and the
    username. Signing in again with the same Supabase account is a new start —
    a fresh user, a fresh empty workspace — not a recovery.

    What this does NOT delete, because it does not own them: the Supabase auth
    record (the identity provider holds that), and `feedback` rows, whose
    `user_id` is `ondelete="SET NULL"` so the text survives, detached, as
    product feedback.

    Idempotent: a second call, or one from a user with nothing stored,
    succeeds with zero deletions.
    """
    bearer = _extract_bearer(authorization)
    if not bearer:
        return json_response(ResponseCode.UNAUTHORIZED, "Missing identity token")

    email = verify_identity_token(bearer)
    if not email:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    email_lower = email.strip().lower()
    erased: list = []

    user = db.execute(select(User).where(User.email == email_lower)).scalar_one_or_none()
    if user is not None:
        # Every workspace they own, not only the active one: a workspace left
        # behind by an earlier soft delete holds exactly the same data.
        owned = db.execute(
            select(Workspace).where(Workspace.owner_user_id == user.id)
        ).scalars().all()
        for ws in owned:
            erased.append(_erase_workspace(db, ws))

    # Email-keyed rows in workspaces the user does not own (legacy/self-hosted
    # multi-user deployments). Their own workspace's copies are already gone
    # with the cascade above.
    channel_memberships_deleted = db.query(ChannelHumanMember).filter(
        ChannelHumanMember.user_email == email_lower
    ).delete(synchronize_session=False)

    devices_deleted = db.query(DeviceToken).filter(
        DeviceToken.user_email == email_lower
    ).delete(synchronize_session=False)

    if user is not None:
        db.flush()                     # let the workspace cascades land first
        db.delete(user)

    db.commit()

    logger.info(
        "account: erased %s (workspaces=%s files=%s events=%s channel_members=%s devices=%s)",
        email_lower, len(erased),
        sum(e["files"] for e in erased), sum(e["events"] for e in erased),
        channel_memberships_deleted, devices_deleted,
    )

    return success_response({
        "email": email_lower,
        "deleted": {
            "ownedWorkspace": len(erased),
            "files": sum(e["files"] for e in erased),
            "events": sum(e["events"] for e in erased),
            "channel_memberships": channel_memberships_deleted,
            "devices": devices_deleted,
        },
    })
