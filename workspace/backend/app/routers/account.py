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

from app.access import get_or_create_owned_workspace, resolve_current_user
from app.database import get_db
from app.firebase_auth import verify_identity_token
from app.models import (
    ChannelHumanMember,
    DeviceToken,
    User,
    Workspace,
)
from app.response import ResponseCode, json_response, success_response
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


def _canonical_workspace_row(ws: Workspace) -> dict:
    return {
        "workspaceId": str(ws.id),
        "name": ws.name,
        "slug": ws.slug,
        # The workspace's machine/access token. Exposed here — but ONLY here,
        # to the verified owner of this exact workspace — because the
        # realtime event stream (EventSource can't send custom headers) still
        # authenticates by this token in the URL; see app/routers/events.py.
        # There is no invite/share/add-collaborator path in the Placement AI
        # product, so this token can never reach anyone but its own owner.
        "token": ws.password_hash,
        "lastActivityAt": ws.last_activity_at.isoformat() if ws.last_activity_at else None,
    }


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

    if not user.username:
        db.commit()
        return json_response(ResponseCode.FORBIDDEN, "Username setup required")

    workspace = get_or_create_owned_workspace(db, user)
    db.commit()
    db.refresh(workspace)

    return success_response(_canonical_workspace_row(workspace))


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

    # Workspace access is unavailable until signup's required username claim
    # has succeeded. The authenticated account remains usable so the client
    # can ask the user to choose another name after an availability race.
    if not user.username:
        db.commit()
        return json_response(ResponseCode.FORBIDDEN, "Username setup required")

    workspace = get_or_create_owned_workspace(db, user)
    db.commit()
    db.refresh(workspace)

    return success_response([{**_canonical_workspace_row(workspace), "role": "owner"}])


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


@router.delete("/account")
def delete_account(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Delete all data belonging to the calling user.

    Identifies the user from the verified identity token's email. Placement AI
    v2.0: the user's own personal workspace (`owner_user_id`) is private to
    them — unlike the old multi-collaborator model, there is no one else who
    could be relying on it — so it is soft-deleted (`status = "deleted"`) here
    rather than left behind as orphaned, inaccessible-but-present data. Also
    removes every email-keyed row from workspaces the user merely
    collaborated in (legacy/self-hosted multi-user workspaces, not their own).
    Idempotent: a second call (or a user with no stored data) succeeds with
    zero deletions. Does not delete the Supabase auth record or the local
    `users` row itself — only this app's data.
    """
    bearer = _extract_bearer(authorization)
    if not bearer:
        return json_response(ResponseCode.UNAUTHORIZED, "Missing identity token")

    email = verify_identity_token(bearer)
    if not email:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid identity token")

    email_lower = email.strip().lower()

    owned_workspace_deleted = 0
    user = db.execute(select(User).where(User.email == email_lower)).scalar_one_or_none()
    if user is not None:
        owned = db.execute(
            select(Workspace).where(
                Workspace.owner_user_id == user.id,
                Workspace.status == "active",
            )
        ).scalars().all()
        for ws in owned:
            ws.status = "deleted"
            owned_workspace_deleted += 1

    channel_memberships_deleted = db.query(ChannelHumanMember).filter(
        ChannelHumanMember.user_email == email_lower
    ).delete(synchronize_session=False)

    devices_deleted = db.query(DeviceToken).filter(
        DeviceToken.user_email == email_lower
    ).delete(synchronize_session=False)

    db.commit()

    logger.info(
        "account: deleted account for %s (owned_workspace=%s channel_members=%s devices=%s)",
        email_lower, owned_workspace_deleted, channel_memberships_deleted, devices_deleted,
    )

    return success_response({
        "email": email_lower,
        "deleted": {
            "ownedWorkspace": owned_workspace_deleted,
            "channel_memberships": channel_memberships_deleted,
            "devices": devices_deleted,
        },
    })
