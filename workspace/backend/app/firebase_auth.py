# -*- coding: utf-8 -*-
"""
Identity token verification for workspace user authentication.

Verifies the access token an end user obtained from their login provider —
Supabase Auth (the canonical human-identity provider for web/desktop/mobile)
or Sign in with Apple (used by the OpenAgents Go iOS app for App Store
guideline 4.8 login parity) — and resolves it to the user's identity claims.
Used alongside workspace-token auth, not as a replacement.

Call `verify_identity_token()` / `verify_identity_claims()` for the
provider-agnostic path; `verify_supabase_claims()` / `verify_apple_claims()`
remain for callers that already know which provider issued the token.

Firebase is intentionally NOT a human-auth provider here — see
`_init_firebase()` below, which exists solely for mobile push (FCM), a
different feature that happens to share the same Admin SDK app.
"""

import json
import logging
import threading
import time

from app.identity_errors import IdentityUnavailable
from typing import Optional

from app.config import config

logger = logging.getLogger(__name__)

_firebase_initialized = False

# Apple's JWKS endpoint + issuer for Sign in with Apple identity tokens.
_APPLE_ISSUER = "https://appleid.apple.com"
_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_apple_jwk_client = None
_apple_jwk_lock = threading.Lock()

# Supabase JWKS (used when the project signs tokens asymmetrically). None
# until the first successful fetch; a project on legacy HS256-only signing
# has no JWKS endpoint at all, in which case we fall back to introspection.
_supabase_jwk_client = None
_supabase_jwk_lock = threading.Lock()
# When the JWKS fetch last failed. Used as a COOLDOWN, not a permanent latch:
# this was a plain boolean that, once set, was never cleared, so a single
# Supabase blip while the backend was starting demoted token verification to a
# per-request network call to Supabase for the lifetime of the process.
_supabase_jwks_failed_at = 0.0
_SUPABASE_JWKS_RETRY_SECONDS = 300.0


# Re-exported so `from app.firebase_auth import IdentityUnavailable` keeps
# working; defined in app/identity_errors.py so that reloading THIS module does
# not mint a new class and unbind main.py's handler for it.
IdentityUnavailable = IdentityUnavailable


def _make_noop_credential():
    """Create a minimal Firebase credential for FCM's Admin SDK app.

    Push (services/fcm_client.py) needs a real service account
    (FIREBASE_CREDENTIALS_JSON); this no-op path only exists so the app can
    still come up in environments that haven't configured push.
    """
    from firebase_admin import credentials as fb_credentials
    import google.auth.credentials

    class _Cred(fb_credentials.Base):
        def get_credential(self):
            return google.auth.credentials.AnonymousCredentials()

    return _Cred()


def _init_firebase() -> bool:
    """Initialize Firebase Admin SDK for FCM push. Returns True if successful.

    Not used for human-auth verification (see module docstring) — only by
    services/fcm_client.py.
    """
    global _firebase_initialized
    if _firebase_initialized:
        return True

    try:
        import firebase_admin
        from firebase_admin import credentials

        # Check if already initialized
        try:
            firebase_admin.get_app()
            _firebase_initialized = True
            return True
        except ValueError:
            pass

        if config.FIREBASE_CREDENTIALS_JSON:
            cred_dict = json.loads(config.FIREBASE_CREDENTIALS_JSON)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        else:
            logger.info("firebase_auth: No Firebase config, skipping init (push disabled)")
            return False

        _firebase_initialized = True
        logger.info("firebase_auth: Firebase Admin SDK initialized (push only)")
        return True
    except Exception as e:
        logger.warning("firebase_auth: Firebase init failed: %s", e)
        return False


def _looks_like_supabase_token(token: str) -> bool:
    """Cheap, signature-free check of the `iss` claim, purely for routing
    (not trust) — mirrors the pattern used for Apple/Supabase-vs-other
    dispatch. Any parse failure means "not ours"."""
    if not token or token.count(".") != 2:
        return False
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False})
        return claims.get("iss") == f"{config.SUPABASE_URL}/auth/v1"
    except Exception:
        return False


def _get_supabase_jwk_client():
    """Lazily build (and cache) a PyJWKClient for the Supabase project's
    signing keys. Returns None if the project has no JWKS endpoint (legacy
    HS256-only signing) — cached so we don't retry every request."""
    global _supabase_jwk_client, _supabase_jwks_failed_at
    if _supabase_jwk_client is not None:
        return _supabase_jwk_client
    if time.monotonic() - _supabase_jwks_failed_at < _SUPABASE_JWKS_RETRY_SECONDS:
        return None
    with _supabase_jwk_lock:
        if _supabase_jwk_client is None:
            from jwt import PyJWKClient

            try:
                client = PyJWKClient(f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json")
                client.get_signing_keys()  # force a fetch now to detect 404s
                _supabase_jwk_client = client
                _supabase_jwks_failed_at = 0.0
            except Exception as e:
                # A project with no JWKS endpoint and a project we merely could
                # not reach look the same here, so back off and try again later
                # rather than deciding permanently.
                logger.warning("firebase_auth: Supabase JWKS unavailable (%s) — retrying in %ss",
                               e, int(_SUPABASE_JWKS_RETRY_SECONDS))
                _supabase_jwks_failed_at = time.monotonic()
    return _supabase_jwk_client


def _verify_supabase_via_introspection(token: str) -> Optional[dict]:
    """Verify a Supabase access token by asking Supabase itself, rather than
    checking a signature locally. Requires only the public anon key (never a
    secret) — the fallback for projects with no JWKS endpoint."""
    try:
        import httpx

        resp = httpx.get(
            f"{config.SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": config.SUPABASE_ANON_KEY,
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        # Supabase looked at the token and said no. That is a real refusal.
        if resp.status_code in (400, 401, 403):
            return None
        # Anything else (429, 5xx) is Supabase failing, not the token failing.
        raise IdentityUnavailable(f"Supabase introspection returned {resp.status_code}")
    except IdentityUnavailable:
        raise
    except Exception as e:
        # Never reached it at all: DNS, timeout, TLS, proxy. Says nothing about
        # the token, so it must not be reported as a bad token.
        logger.warning("firebase_auth: Supabase introspection unreachable: %s", e)
        raise IdentityUnavailable(str(e)) from e


def verify_supabase_claims(token: str) -> Optional[dict]:
    """Verify a Supabase access token and return persisted identity claims.

    Returns {"provider", "email", "supabase_uid", "display_name"} or None.
    Tries local JWKS verification first (no network round trip once cached);
    falls back to asking Supabase to verify the token itself
    (GET /auth/v1/user) for projects that sign with a shared secret we
    deliberately never hold. Never raises.
    """
    if not _looks_like_supabase_token(token):
        return None

    claims = None
    jwk_client = _get_supabase_jwk_client()
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
        except Exception as e:
            # Could be a bad signature, an expired token, or a key we do not
            # have yet after a rotation. Introspection below tells them apart.
            logger.warning("firebase_auth: Supabase JWKS verification failed: %s", e)
            claims = None

    if claims is None:
        # Raises IdentityUnavailable when Supabase could not be asked.
        claims = _verify_supabase_via_introspection(token)

    if not claims:
        return None

    email = (claims.get("email") or "").strip().lower()
    if not email:
        logger.warning("firebase_auth: Supabase token valid but no email claim")
        return None

    user_metadata = claims.get("user_metadata") or {}
    return {
        "provider": "supabase",
        "email": email,
        "supabase_uid": claims.get("sub") or claims.get("id"),
        "display_name": user_metadata.get("username") or user_metadata.get("display_name"),
    }


def _apple_client_ids() -> list:
    """Allowed `aud` values for Apple identity tokens (native bundle id + any
    Services IDs), parsed from the comma-separated APPLE_CLIENT_IDS config."""
    return [c.strip() for c in config.APPLE_CLIENT_IDS.split(",") if c.strip()]


def _get_apple_jwk_client():
    """Lazily build (and cache) a PyJWKClient for Apple's signing keys.

    PyJWKClient caches fetched keys in-process and re-fetches on a cache miss
    (e.g. after Apple rotates keys), so one client instance is reused for the
    life of the process."""
    global _apple_jwk_client
    if _apple_jwk_client is None:
        with _apple_jwk_lock:
            if _apple_jwk_client is None:
                from jwt import PyJWKClient
                _apple_jwk_client = PyJWKClient(_APPLE_JWKS_URL)
    return _apple_jwk_client


def verify_apple_token(token: str) -> Optional[str]:
    """
    Verify a Sign in with Apple identity token and return the user's email.

    Validates the RS256 signature against Apple's published JWKS, the issuer
    (`https://appleid.apple.com`) and the audience (the app's bundle id /
    Services ID from APPLE_CLIENT_IDS). Returns None on any failure.

    Note: Apple only includes the `email` claim when the app requested the
    email scope at first consent; it continues to return it on later sign-ins.
    A user who chose "Hide My Email" gets a private relay address, which is
    still a stable per-app identifier we can key on.
    """
    client_ids = _apple_client_ids()
    if not client_ids:
        logger.warning("firebase_auth: APPLE_CLIENT_IDS not configured, cannot verify Apple token")
        return None

    try:
        import jwt

        signing_key = _get_apple_jwk_client().get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_ids,
            issuer=_APPLE_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
        email = decoded.get("email")
        if not email:
            logger.warning("firebase_auth: Apple token valid but no email claim")
            return None
        logger.info("firebase_auth: Verified Apple token for %s", email)
        return email
    except Exception as e:
        logger.warning("firebase_auth: Apple token verification failed: %s", e)
        return None


def verify_identity_token(token: str) -> Optional[str]:
    """
    Verify an end-user identity token from any supported login provider and
    return the user's email.

    Tries Supabase (the canonical human-identity provider) first, then Sign in
    with Apple (the iOS app). Returns None if no provider accepts the token.
    This is the provider-agnostic entry point new callers should use.
    """
    claims = verify_supabase_claims(token)
    if claims:
        return claims["email"]
    return verify_apple_token(token)


def verify_apple_claims(token: str) -> Optional[dict]:
    """Verify a Sign in with Apple identity token and return persisted claims.

    Returns {"provider", "email", "apple_sub", "display_name"} or None. Apple
    identity tokens carry no name claim (the name is only returned once, in the
    authorization response, not the token), so display_name is always None here.
    """
    client_ids = _apple_client_ids()
    if not client_ids:
        return None
    try:
        import jwt

        signing_key = _get_apple_jwk_client().get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_ids,
            issuer=_APPLE_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
        email = decoded.get("email")
        if not email:
            return None
        return {
            "provider": "apple",
            "email": email,
            "apple_sub": decoded.get("sub"),
            "display_name": None,
        }
    except Exception as e:
        logger.warning("firebase_auth: Apple claims verification failed: %s", e)
        return None


def verify_identity_claims(token: str) -> Optional[dict]:
    """Provider-agnostic identity verification returning persisted claims.

    Tries Supabase (the canonical human-identity provider) then Sign in with
    Apple. Returns a dict with keys email, supabase_uid, apple_sub,
    display_name, provider (missing-provider ids are None), or None if
    neither provider accepts the token. This is the entry point for user-row
    resolution (app/access.py)."""
    claims = verify_supabase_claims(token)
    if claims:
        return claims
    return verify_apple_claims(token)
