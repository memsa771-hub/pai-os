from unittest.mock import patch

from app.security import human_auth


def test_supabase_claims_are_normalized() -> None:
    raw = {
        "id": "user-123",
        "email": "  Student@Example.COM ",
        "user_metadata": {"username": "student", "display_name": "Ignored"},
    }
    with (
        patch.object(human_auth, "_looks_like_supabase_token", return_value=True),
        patch.object(human_auth, "_get_jwk_client", return_value=None),
        patch.object(human_auth, "_introspect", return_value=raw),
    ):
        claims = human_auth.verify_identity_claims("token")

    assert claims == {
        "provider": "supabase",
        "email": "student@example.com",
        "supabase_uid": "user-123",
        "username": "student",
        "display_name": "student",
    }


def test_non_supabase_token_is_rejected_without_introspection() -> None:
    with (
        patch.object(human_auth, "_looks_like_supabase_token", return_value=False),
        patch.object(human_auth, "_introspect") as introspect,
    ):
        assert human_auth.verify_identity_claims("other-provider-token") is None

    introspect.assert_not_called()


def test_identity_token_returns_supabase_email() -> None:
    with patch.object(
        human_auth,
        "verify_identity_claims",
        return_value={"email": "student@example.com"},
    ):
        assert human_auth.verify_identity_token("token") == "student@example.com"
