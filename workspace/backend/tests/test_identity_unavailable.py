# -*- coding: utf-8 -*-
"""Could-not-verify is not the same as refused, and must not be a 401.

A client that gets 401 is right to conclude its session is over — both of ours
do exactly that, ending the session and returning to sign-in. So answering 401
because Supabase was briefly unreachable signs a student out for OUR outage,
mid-session, with nothing on screen to explain it.

`verify_supabase_claims` used to return None for both "Supabase says this token
is bad" and "we could not reach Supabase", which made the two indistinguishable
to every caller.
"""

import httpx
import pytest

import app.firebase_auth as firebase_auth


def _unavailable_cls():
    """Resolved through the module, never captured at import.

    tests/test_identity_defaults.py reloads app.firebase_auth, which mints a
    NEW IdentityUnavailable class object. A `from ... import` here would hold
    the old one and then fail to match the exception actually raised — the
    failure looks like "did not raise" while the traceback plainly shows it
    being raised.
    """
    return firebase_auth.IdentityUnavailable


@pytest.fixture
def no_jwks(monkeypatch):
    """Force the introspection path, which is where the two cases meet.

    Not autouse: TestJwksBackoffIsNotPermanent exercises the real
    _get_supabase_jwk_client, which this would shadow.
    """
    monkeypatch.setattr(firebase_auth, "_get_supabase_jwk_client", lambda: None)


def _introspection_returns(monkeypatch, *, status=None, error=None):
    class FakeResponse:
        status_code = status

        @staticmethod
        def json():
            return {"email": "student@example.com"}

    def fake_get(*_a, **_kw):
        if error is not None:
            raise error
        return FakeResponse()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", fake_get)


class TestRefusalIsARefusal:
    @pytest.mark.parametrize("status", [400, 401, 403])
    def test_supabase_saying_no_is_a_rejection(self, monkeypatch, no_jwks, status):
        _introspection_returns(monkeypatch, status=status)
        assert firebase_auth._verify_supabase_via_introspection("tok") is None

    def test_supabase_saying_yes_returns_claims(self, monkeypatch, no_jwks):
        _introspection_returns(monkeypatch, status=200)
        assert firebase_auth._verify_supabase_via_introspection("tok") == {
            "email": "student@example.com"
        }


class TestUnreachableIsNotARefusal:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_provider_failing_raises_rather_than_rejecting(self, monkeypatch, no_jwks, status):
        _introspection_returns(monkeypatch, status=status)
        with pytest.raises(_unavailable_cls()):
            firebase_auth._verify_supabase_via_introspection("tok")

    @pytest.mark.parametrize("error", [
        httpx.ConnectError("dns"),
        httpx.ReadTimeout("slow"),
        OSError("network is unreachable"),
    ])
    def test_never_reaching_the_provider_raises(self, monkeypatch, no_jwks, error):
        _introspection_returns(monkeypatch, error=error)
        with pytest.raises(_unavailable_cls()):
            firebase_auth._verify_supabase_via_introspection("tok")


class TestTheApiAnswers503:
    def test_unverifiable_identity_is_503_not_401(self, client, monkeypatch):
        """The whole point: a client must not read this as 'you are signed out'."""
        def unavailable(_token):
            raise _unavailable_cls()("supabase unreachable")

        monkeypatch.setattr(firebase_auth, "verify_supabase_claims", unavailable)
        monkeypatch.setattr(firebase_auth, "verify_identity_claims", unavailable)
        import app.access as access
        monkeypatch.setattr(access, "verify_identity_claims", unavailable)

        resp = client.get("/v1/account/workspace",
                          headers={"Authorization": "Bearer some-token"})
        assert resp.status_code == 503
        assert resp.status_code != 401


class TestJwksBackoffIsNotPermanent:
    def test_a_failed_fetch_is_retried_later(self, monkeypatch):
        """This was a boolean that, once set, was never cleared — one blip at
        startup demoted every request to a network call for the whole process."""
        firebase_auth._supabase_jwk_client = None
        firebase_auth._supabase_jwks_failed_at = 0.0
        assert firebase_auth._SUPABASE_JWKS_RETRY_SECONDS > 0
        # Inside the cooldown: no retry.
        import time as _time
        firebase_auth._supabase_jwks_failed_at = _time.monotonic()
        assert firebase_auth._get_supabase_jwk_client() is None
        # Past it: the client is rebuilt (here the build fails again, but the
        # point is that it was ATTEMPTED rather than latched off forever).
        attempts = []
        monkeypatch.setattr(firebase_auth, "config", firebase_auth.config)
        firebase_auth._supabase_jwks_failed_at = (
            _time.monotonic() - firebase_auth._SUPABASE_JWKS_RETRY_SECONDS - 1
        )
        import jwt as _jwt

        class Boom:
            def __init__(self, *a, **k):
                attempts.append(1)
                raise RuntimeError("still down")

        monkeypatch.setattr(_jwt, "PyJWKClient", Boom)
        firebase_auth._get_supabase_jwk_client()
        assert attempts, "expired cooldown must retry the JWKS fetch"
