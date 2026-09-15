# -*- coding: utf-8 -*-
"""
Auth endpoints that need server-side help beyond a plain Supabase bearer.

POST /v1/auth/claim-username     Attach a unique username to the signed-in
                                  Supabase user (authenticated).
POST /v1/auth/sign-in-username   Sign in with username+password when the
                                  caller doesn't have the account's email
                                  (public, rate-limited).

Everything else (email/password sign-in, sign-up, OAuth, session refresh)
happens directly between the client and Supabase Auth — this router only
covers the two things Supabase itself can't do (username lookup, and the
uniqueness constraint on our own `username` column).
"""

import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import config
from app.database import get_db
from app.firebase_auth import verify_identity_claims
from app.access import extract_bearer, get_or_create_user
from app.models import User
from app.response import ResponseCode, json_response, success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["Auth"])

_USERNAME_MIN_LENGTH = 3
_USERNAME_MAX_LENGTH = 32


def _normalize_username(raw: str) -> Optional[str]:
    username = (raw or "").strip().lower()
    if not (_USERNAME_MIN_LENGTH <= len(username) <= _USERNAME_MAX_LENGTH):
        return None
    if not all(c.isalnum() or c in "_-" for c in username):
        return None
    return username


class ClaimUsernameRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)


@router.post("/username-available")
def username_available(body: ClaimUsernameRequest, db: Session = Depends(get_db)):
    """Validate a username and report availability without exposing accounts."""
    username = _normalize_username(body.username)
    if not username:
        return json_response(
            ResponseCode.BAD_REQUEST,
            f"Username must be {_USERNAME_MIN_LENGTH}-{_USERNAME_MAX_LENGTH} characters: letters, numbers, _ or -",
        )
    existing = db.execute(
        select(User.id).where(func.lower(User.username) == username)
    ).first()
    return success_response({"username": username, "available": existing is None})


@router.post("/claim-username")
def claim_username(
    body: ClaimUsernameRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """Attach a unique username to the caller's account.

    Idempotent: re-claiming the same username you already own succeeds
    (called both right after signup and again after email confirmation, once
    a real session exists).
    """
    bearer = extract_bearer(authorization)
    claims = verify_identity_claims(bearer) if bearer else None
    if not claims:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid or expired token")

    username = _normalize_username(body.username)
    if not username:
        return json_response(
            ResponseCode.BAD_REQUEST,
            f"Username must be {_USERNAME_MIN_LENGTH}-{_USERNAME_MAX_LENGTH} characters: letters, numbers, _ or -",
        )

    user = get_or_create_user(db, claims)
    if user is None:
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid or expired token")

    if user.username == username:
        db.commit()
        return success_response({"username": username})

    existing = db.execute(
        select(User).where(func.lower(User.username) == username, User.id != user.id)
    ).scalar_one_or_none()
    if existing is not None:
        db.rollback()
        return json_response(ResponseCode.CONFLICT, "Username is already taken")

    user.username = username
    try:
        db.commit()
    except IntegrityError:
        # The availability check is advisory. The unique index is the final
        # authority when two signups race for the same normalized username.
        db.rollback()
        return json_response(ResponseCode.CONFLICT, "Username is already taken")
    return success_response({"username": username})


# ---------------------------------------------------------------------------
# Username sign-in — resolves username -> email server-side only. The client
# never learns the email; a wrong username and a wrong password return the
# identical generic error.
# ---------------------------------------------------------------------------

_GENERIC_SIGN_IN_ERROR = "Invalid username or password"

# Sign-in attempts by client IP in the last hour (per process, sliding
# window) — mirrors the pilot-grant rate limiter in app/routers/pilot.py.
_recent_attempts: dict = defaultdict(deque)


def _attempt_rate_ok(key: str) -> bool:
    now = time.monotonic()
    bucket = _recent_attempts[key]
    while bucket and now - bucket[0] > 3600:
        bucket.popleft()
    if len(bucket) >= config.SIGN_IN_USERNAME_MAX_ATTEMPTS_PER_HOUR:
        return False
    bucket.append(now)
    return True


class SignInUsernameRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=4096)


@router.post("/sign-in-username")
def sign_in_username(body: SignInUsernameRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not _attempt_rate_ok(client_ip):
        return json_response(
            ResponseCode.BAD_REQUEST, "Too many attempts. Please try again later.", status_code=429
        )

    username = _normalize_username(body.username)
    if not username:
        return json_response(ResponseCode.UNAUTHORIZED, _GENERIC_SIGN_IN_ERROR)

    user = db.execute(
        select(User).where(func.lower(User.username) == username)
    ).scalar_one_or_none()

    if user is None:
        return json_response(ResponseCode.UNAUTHORIZED, _GENERIC_SIGN_IN_ERROR)

    try:
        import httpx

        resp = httpx.post(
            f"{config.SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": config.SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": user.email, "password": body.password},
            timeout=10.0,
        )
    except Exception as e:
        logger.warning("auth: sign-in-username Supabase call failed: %s", e)
        return json_response(ResponseCode.INTERNAL_ERROR, "Sign-in is temporarily unavailable")

    if resp.status_code != 200:
        return json_response(ResponseCode.UNAUTHORIZED, _GENERIC_SIGN_IN_ERROR)

    payload = resp.json()
    # Never relay Supabase's user object: it contains the email resolved from
    # the username above. Clients can use the access token with /auth/v1/user.
    return success_response({
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "expires_in": payload.get("expires_in"),
        "token_type": payload.get("token_type", "bearer"),
    })
