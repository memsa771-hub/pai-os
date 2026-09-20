"""First-run onboarding: canonical writes, and the refinement trap it avoids.

The load-bearing test here is
`test_onboarding_category_does_not_freeze_the_living_status`. Under
`latest_wins` a `user_explicit` fact can never be superseded by conversation
extraction, so had onboarding written its dropdown answer into
`identity.current_status`, PAI would have been permanently unable to improve
"Student" into "Final-year BS Computer Science student". That is a silent,
ship-it-and-never-notice failure, so it gets an explicit test.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.memory.errors import MemoryDataError
from app.memory.field_definitions import SEED_FIELD_DEFINITIONS, VaultFieldDefinitionService
from app.memory.onboarding import ONBOARDING_FIELDS, OnboardingService
from app.memory.student_profile_view import StudentProfileView
from app.memory.student_records import ENTITY_MODELS
from app.memory.vault import VaultService
from app.models import (
    BackgroundJob, EventRecord, FileRecord, MemoryCandidate, PaiEpisode, PaiMemory,
    ProfileIssue, StudentRecordRevision, User, VaultFact, VaultFieldDefinition, Workspace,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


# Mirrors migrations 063 and 066 — one field of each sensitivity class, plus
# the two the onboarding flow introduces.
FIELD_SPECS = (
    ("identity.full_name", "sensitive", None),
    ("identity.preferred_name", "normal", None),
    ("identity.current_status", "normal", None),
    ("identity.nationality", "sensitive", None),
    ("identity.date_of_birth", "restricted", None),
    ("location.current_country", "normal", None),
    ("location.current_city", "normal", None),
    ("identity.gender", "sensitive", ["male", "female", "other", "undisclosed"]),
    ("identity.status_category", "normal", ["student", "professional", "other"]),
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _setup(connection, record):
        connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())
        connection.execute("PRAGMA foreign_keys=ON")

    tables = [User, Workspace, EventRecord, FileRecord, VaultFact, VaultFieldDefinition,
              MemoryCandidate, PaiMemory, PaiEpisode, ProfileIssue, StudentRecordRevision,
              BackgroundJob, *ENTITY_MODELS.values()]
    Base.metadata.create_all(engine, tables=[m.__table__ for m in tables])

    with Session(engine, autoflush=False) as session:
        user = User(email="new@example.test", username="alikhan")
        session.add(user)
        session.flush()
        workspace = Workspace(name="Student", owner_user_id=user.id)
        session.add(workspace)
        session.flush()
        fields = VaultFieldDefinitionService(session)
        for spec in SEED_FIELD_DEFINITIONS:
            fields.upsert_definition(spec)
        for key, sensitivity, choices in FIELD_SPECS:
            schema = {"type": "string"}
            if choices:
                schema["enum"] = choices
            fields.upsert_definition({
                "key": key, "category": key.split(".")[0], "sensitivity": sensitivity,
                "data_type": "string", "validation_schema": schema,
                # Matches migration 063: restricted identifiers are review-gated.
                "conflict_policy": "manual_review" if sensitivity == "restricted" else "latest_wins",
            })
        session.commit()
        session.info.update(workspace=workspace, user=user)
        yield session
    engine.dispose()


ANSWERS = {
    "fullName": "Ali Ahmed",
    "preferredName": "Ali",
    "statusCategory": "student",
    "nationality": "Pakistani",
    "gender": "male",
    "dateOfBirth": "2003-04-15",
    "currentCountry": "Pakistan",
    "currentCity": "Islamabad",
}


def test_onboarding_is_required_until_it_is_completed(db):
    service = OnboardingService(db)
    workspace = db.info["workspace"]

    assert service.state(workspace)["required"] is True
    service.apply(workspace, ANSWERS)
    db.commit()

    state = service.state(workspace)
    assert state["required"] is False
    assert state["completedAt"]


def test_answers_land_in_the_vault_as_first_hand_claims(db):
    workspace = db.info["workspace"]
    result = OnboardingService(db).apply(workspace, ANSWERS)
    db.commit()
    assert result["rejected"] == {}, result["rejected"]

    vault = VaultService(db)
    facts = vault.snapshot(str(workspace.id), include_sensitive=True)
    assert facts["identity.full_name"] == "Ali Ahmed"
    assert facts["identity.preferred_name"] == "Ali"
    assert facts["identity.status_category"] == "student"
    assert facts["identity.nationality"] == "Pakistani"
    assert facts["identity.gender"] == "male"
    assert facts["location.current_city"] == "Islamabad"

    # Provenance: these are the student's own words, not an inference.
    fact = vault.get_fact(str(workspace.id), "identity.full_name")
    assert fact.source_type == "user_explicit"
    assert fact.claim_origin == "student"
    assert fact.evidence["capture"] == "onboarding"


def test_review_gated_date_of_birth_applies_because_the_student_stated_it(db):
    """`manual_review` admits exactly one source — a first-hand statement."""
    workspace = db.info["workspace"]
    OnboardingService(db).apply(workspace, ANSWERS)
    db.commit()

    facts = VaultService(db).snapshot(str(workspace.id), include_sensitive=True)
    assert facts["identity.date_of_birth"] == "2003-04-15"
    # It applied outright rather than parking as something to confirm later.
    assert not db.query(ProfileIssue).filter_by(workspace_id=str(workspace.id)).count()


def test_onboarding_category_does_not_freeze_the_living_status(db):
    """The whole reason `identity.status_category` exists.

    Onboarding's coarse answer must not occupy `identity.current_status`,
    because a `user_explicit` fact there could never be refined by extraction.
    """
    workspace = db.info["workspace"]
    workspace_id = str(workspace.id)
    OnboardingService(db).apply(workspace, ANSWERS)
    db.commit()

    vault = VaultService(db)
    facts = vault.snapshot(workspace_id, include_sensitive=True)
    assert facts["identity.status_category"] == "student"
    assert "identity.current_status" not in facts, \
        "onboarding must leave current_status free for PAI to learn"

    # PAI later hears something richer in conversation — it must stick.
    vault.apply_fact(workspace_id, "identity.current_status",
                     "Final-year BS Computer Science student", "conversation")
    db.commit()
    refined = vault.snapshot(workspace_id, include_sensitive=True)
    assert refined["identity.current_status"] == "Final-year BS Computer Science student"
    assert refined["identity.status_category"] == "student"


def test_profile_shows_onboarding_facts_and_still_hides_date_of_birth(db):
    workspace = db.info["workspace"]
    OnboardingService(db).apply(workspace, ANSWERS)
    db.commit()

    profile = StudentProfileView(db).build(str(workspace.id), {"displayName": "Ali Ahmed"})
    assert profile["header"]["location"] == "Islamabad, Pakistan"
    # No richer status yet, so the header falls back to the category.
    assert profile["header"]["status"] == "Student"
    assert profile["facts"]["identity.gender"] == "male"
    # Date of birth is `restricted` and must not reach the Profile surface.
    assert "identity.date_of_birth" not in profile["facts"]
    assert "2003-04-15" not in str(profile)


def test_required_answers_are_enforced_and_nothing_is_half_written(db):
    workspace = db.info["workspace"]
    service = OnboardingService(db)
    with pytest.raises(MemoryDataError, match="statusCategory"):
        service.apply(workspace, {"fullName": "Ali Ahmed", "nationality": "Pakistani"})
    # Validation runs over the whole set first, so the valid answers in that
    # same submission were never written.
    assert VaultService(db).snapshot(str(workspace.id), include_sensitive=True) == {}
    assert service.state(workspace)["required"] is True


@pytest.mark.parametrize("answers,message", [
    ({"fullName": "A", "statusCategory": "astronaut"}, "statusCategory"),
    ({"fullName": "A", "statusCategory": "student", "gender": "yes"}, "gender"),
    ({"fullName": "A", "statusCategory": "student", "dateOfBirth": "15-04-2003"}, "dateOfBirth"),
    ({"fullName": "A", "statusCategory": "student", "dateOfBirth": "2099-01-01"}, "dateOfBirth"),
    ({"fullName": "A", "statusCategory": "student", "favouriteColour": "blue"}, "Unknown"),
])
def test_bad_answers_are_rejected_with_the_field_named(db, answers, message):
    with pytest.raises(MemoryDataError, match=message):
        OnboardingService(db).apply(db.info["workspace"], answers)


def test_skipping_never_asks_again_and_writes_nothing(db):
    workspace = db.info["workspace"]
    service = OnboardingService(db)
    service.skip(workspace)
    db.commit()

    assert service.state(workspace)["required"] is False
    assert VaultService(db).snapshot(str(workspace.id), include_sensitive=True) == {}


def test_returning_student_sees_their_own_values_prefilled(db):
    workspace = db.info["workspace"]
    service = OnboardingService(db)
    service.apply(workspace, ANSWERS)
    db.commit()

    prefill = service.state(workspace)["prefill"]
    assert prefill["fullName"] == "Ali Ahmed"
    assert prefill["statusCategory"] == "student"
    assert prefill["currentCity"] == "Islamabad"


def test_untouched_account_prefills_preferred_name_from_the_login_handle(db):
    prefill = OnboardingService(db).state(db.info["workspace"])["prefill"]
    assert prefill["preferredName"] == "alikhan"


def test_completing_onboarding_twice_keeps_the_first_timestamp(db):
    workspace = db.info["workspace"]
    service = OnboardingService(db)
    service.apply(workspace, ANSWERS)
    db.commit()
    first = service.state(workspace)["completedAt"]

    service.apply(workspace, {**ANSWERS, "currentCity": "Lahore"})
    db.commit()
    assert service.state(workspace)["completedAt"] == first
    # ...but a corrected answer still supersedes the old fact.
    facts = VaultService(db).snapshot(str(workspace.id), include_sensitive=True)
    assert facts["location.current_city"] == "Lahore"


def test_display_name_is_seeded_from_the_full_name_but_never_overwritten(db):
    workspace, user = db.info["workspace"], db.info["user"]
    assert user.display_name is None
    OnboardingService(db).apply(workspace, ANSWERS)
    db.commit()
    assert user.display_name == "Ali Ahmed"

    user.display_name = "Ali A."
    db.commit()
    OnboardingService(db).apply(workspace, {**ANSWERS, "fullName": "Ali Ahmed Khan"})
    db.commit()
    assert user.display_name == "Ali A.", "a name the student chose must win"


def test_every_declared_field_maps_to_a_real_vault_key(db):
    """A question with no field definition would fail silently at reconcile."""
    fields = VaultFieldDefinitionService(db)
    for field in ONBOARDING_FIELDS:
        assert fields.get(field.field_key) is not None, field.field_key
