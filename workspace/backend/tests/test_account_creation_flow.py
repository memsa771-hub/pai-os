"""Account creation reaches PAI onboarding without a second username gate."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.access import get_or_create_user
from app.database import Base
from app.models import User


def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__])
    return Session(engine, autoflush=False)


def test_signup_username_is_attached_when_user_is_created():
    with _session() as db:
        user = get_or_create_user(db, {
            "email": "student@example.test",
            "supabase_uid": "signup-user",
            "username": "AliKhan",
        })
        assert user is not None
        assert user.username == "alikhan"


def test_oauth_user_gets_a_silent_stable_username():
    claims = {"email": "new.student@example.test", "supabase_uid": "oauth-user-123"}
    with _session() as db:
        first = get_or_create_user(db, claims)
        assert first is not None
        generated = first.username
        assert generated and 3 <= len(generated) <= 32
        db.commit()

        again = get_or_create_user(db, claims)
        assert again is not None
        assert again.username == generated


def test_legacy_user_without_username_is_backfilled_silently():
    with _session() as db:
        db.add(User(email="legacy@example.test", supabase_uid="legacy-user"))
        db.commit()

        user = get_or_create_user(db, {
            "email": "legacy@example.test",
            "supabase_uid": "legacy-user",
        })
        assert user is not None
        assert user.username and user.username.startswith("legacy_")
