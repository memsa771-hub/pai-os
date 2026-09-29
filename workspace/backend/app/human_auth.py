# -*- coding: utf-8 -*-
"""Central Supabase authentication boundary for human users.

Every backend caller verifies human bearer tokens through this module.  The
application deliberately has one identity provider: Supabase Auth.  OAuth
providers configured inside Supabase still produce ordinary Supabase access
tokens and therefore follow this same path.
"""

import logging
import threading
import time
from typing import Optional

from app.config import config
from app.identity_errors import IdentityUnavailable

logger = logging.getLogger(__name__)

_jwk_client = None
_jwk_lock = threading.Lock()
_jwks_failed_at = 0.0
_JWKS_RETRY_SECONDS = 300.0


def _looks_like_supabase_token(token: str) -> bool:
    """Inspect the unsigned issuer only to route tokens, never to trust them."""
    if not token or token.count(".") != 2 or not config.SUPABASE_URL:
        return False
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False})
        return claims.get("iss") == f"{config.SUPABASE_URL}/auth/v1"
    except Exception:
        return False


def _get_jwk_client():
    """Return a cached Supabase JWKS client, or None during its retry window."""
    global _jwk_client, _jwks_failed_at
    if _jwk_client is not None:
        return _jwk_client
    if time.monotonic() - _jwks_failed_at < _JWKS_RETRY_SECONDS:
        return None
    with _jwk_lock:
        if _jwk_client is None:
            from jwt import PyJWKClient

            try:
                client = PyJWKClient(
                    f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
                )
                client.get_signing_keys()
                _jwk_client = client
                _jwks_failed_at = 0.0
            except Exception as exc:
                logger.warning(
                    "human_auth: Supabase JWKS unavailable (%s); retrying in %ss",
                    exc,
                    int(_JWKS_RETRY_SECONDS),
                )
                _jwks_failed_at = time.monotonic()
    return _jwk_client


def _introspect(token: str) -> Optional[dict]:
    """Ask Supabase to verify a token when local asymmetric keys are unavailable."""
    if not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
        raise IdentityUnavailable("Supabase authentication is not configured")
    try:
        import httpx

        response = httpx.get(
            f"{config.SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": config.SUPABASE_ANON_KEY,
            },
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code in (400, 401, 403):
            return None
        raise IdentityUnavailable(
            f"Supabase introspection returned {response.status_code}"
        )
    except IdentityUnavailable:
        raise
    except Exception as exc:
        logger.warning("human_auth: Supabase introspection unreachable: %s", exc)
        raise IdentityUnavailable(str(exc)) from exc


def verify_identity_claims(token: str) -> Optional[dict]:
    """Verify a Supabase access token and return normalized application claims."""
    if not _looks_like_supabase_token(token):
        return None

    claims = None
    jwk_client = _get_jwk_client()
    if jwk_client is not None:
        try:
            import jwt

            signing_key = jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience="authenticated",
                issuer=f"{config.SUPABASE_URL}/auth/v1",
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:
            # Introspection distinguishes a rejected token from key rotation.
            logger.warning("human_auth: local Supabase verification failed: %s", exc)

    if claims is None:
        claims = _introspect(token)
    if not claims:
        return None

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        logger.warning("human_auth: Supabase token has no email claim")
        return None

    metadata = claims.get("user_metadata") or {}
    return {
        "provider": "supabase",
        "email": email,
        "supabase_uid": claims.get("sub") or claims.get("id"),
        "username": metadata.get("username"),
        "display_name": metadata.get("username") or metadata.get("display_name"),
    }


def verify_identity_token(token: str) -> Optional[str]:
    """Verify a Supabase access token and return its normalized email."""
    claims = verify_identity_claims(token)
    return claims["email"] if claims else None
