"""Profile completion, journey gaps, conflict resolution and collection writes."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import config
from app.database import Base, set_session_factory
from app.memory.candidates import MemoryCandidateService
from app.memory.education_journey import EducationJourneyService
from app.memory.profile_completion import ProfileCompletionService
from app.memory.profile_issues import ProfileIssueService
from app.memory.profile_requirements import ProfileRequirementRegistry
from app.memory.reconciler import MemoryReconciler
from app.memory.student_records import ENTITY_MODELS, StudentRecordService
from app.memory.student_snapshot import StudentSnapshotService
from app.models import (
    MemoryCandidate, ProfileIssue, ProfileRequirement, StudentRecordRevision,
    User, VaultFact, VaultFieldDefinition, Workspace,
)
from app.services import pai
from app.tools.builtin.memory import answer_profile_requirement
from app.tools.context import ToolContext


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def profile_db(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def setup(connection, record):
        connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())
        connection.execute("PRAGMA foreign_keys=ON")

    models = [
        User, Workspace, VaultFieldDefinition, VaultFact, MemoryCandidate,
        ProfileIssue, ProfileRequirement, StudentRecordRevision,
        *ENTITY_MODELS.values(),
    ]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    factory = lambda: Session(engine, autoflush=False)
    previous_factory = set_session_factory(factory)
    session = factory()
    user = User(email="completion@example.test", created_at=datetime.now(timezone.utc))
    session.add(user)
    session.flush()
    workspace = Workspace(name="Completion", owner_user_id=user.id,
                          created_at=datetime.now(timezone.utc))
    session.add(workspace)
    session.commit()
    session.info.update(workspace=str(workspace.id), user=str(user.id))
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "shadow")
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_AT", "")
    yield session
    session.close()
    set_session_factory(previous_factory)
    engine.dispose()


def _requirement(db, key, tier="critical", source_type="vault_fact", source_key=None,
                 source_path=None, selector="any", priority=50):
    row = ProfileRequirement(
        key=key, tier=tier, source_type=source_type, source_key=source_key or key,
        source_path=source_path, selector=selector, question=f"Question for {key}?",
        priority=priority, version=1, enabled=True,
    )
    db.add(row)
    return row


def _fact(db, workspace_id, key, value):
    db.add(VaultFact(
        workspace_id=workspace_id, field_key=key, value={"value": value},
        confidence=1.0, source_type="user_explicit", status="active",
    ))


@pytest.mark.parametrize("filled,eligible", [
    ((99, 100, 100), False),
    ((100, 79, 100), False),
    ((100, 80, 19), False),
    ((100, 80, 20), True),
])
def test_completion_threshold_edges(profile_db, filled, eligible):
    workspace = profile_db.info["workspace"]
    for tier, total, count in zip(("critical", "important", "enrichment"), (100, 100, 100), filled):
        for index in range(total):
            key = f"threshold.{tier}.{index}"
            _requirement(profile_db, key, tier)
            if index < count:
                _fact(profile_db, workspace, key, True)
    profile_db.commit()
    result = ProfileCompletionService(profile_db).evaluate(workspace)
    assert result["personalizedCounselingEligible"] is eligible


def test_zero_tier_and_false_zero_are_filled(profile_db):
    workspace = profile_db.info["workspace"]
    _requirement(profile_db, "answers.false", "critical")
    _requirement(profile_db, "answers.zero", "important")
    _fact(profile_db, workspace, "answers.false", False)
    _fact(profile_db, workspace, "answers.zero", 0)
    profile_db.commit()
    result = ProfileCompletionService(profile_db).evaluate(workspace)
    assert result["tiers"]["enrichment"] == {
        "filled": 0, "total": 0, "percentage": 100, "satisfied": True,
    }
    assert result["personalizedCounselingEligible"] is True


def test_registry_uses_highest_enabled_requirement_version(profile_db):
    first = _requirement(profile_db, "versioned", priority=10)
    second = _requirement(profile_db, "versioned", priority=20)
    second.version = 2
    disabled = _requirement(profile_db, "versioned", priority=30)
    disabled.version = 3
    disabled.enabled = False
    profile_db.commit()

    active = ProfileRequirementRegistry(profile_db).get("versioned")
    assert active.id != first.id
    assert active.version == 2 and active.priority == 20


def _education(db, **values):
    return StudentRecordService(db).apply(
        db.info["workspace"], "education", values,
        source_type="user_explicit", claim_origin="student",
        capture_method="explicit_correction",
    )


def test_journey_detects_broad_gap_without_inventing_record(profile_db):
    _education(profile_db, qualification_name="MSc AI", canonical_level="master",
               academic_status="completed")
    snapshot = StudentSnapshotService(profile_db).build(profile_db.info["workspace"])
    journey = EducationJourneyService().evaluate(snapshot)
    assert [gap["key"] for gap in journey["gaps"]] == ["undergraduate_history"]
    assert len(snapshot.records["education"]) == 1


def test_unknown_and_planned_routes_are_not_guessed(profile_db):
    _education(profile_db, qualification_name="International programme", canonical_level="other")
    snapshot = StudentSnapshotService(profile_db).build(profile_db.info["workspace"])
    journey = EducationJourneyService().evaluate(snapshot)
    assert journey["gaps"][0]["status"] == "clarification_required"
    assert not any(gap["key"] == "undergraduate_history" for gap in journey["gaps"])

    profile_db.rollback()
    for row in StudentRecordService(profile_db).list(profile_db.info["workspace"], "education"):
        row.status = "superseded"
    _education(profile_db, qualification_name="Planned BS", canonical_level="bachelor",
               academic_status="planned")
    snapshot = StudentSnapshotService(profile_db).build(profile_db.info["workspace"])
    assert not EducationJourneyService().evaluate(snapshot)["gaps"]


def test_conflict_links_candidate_and_reconciles_before_close(profile_db):
    workspace = profile_db.info["workspace"]
    profile_db.add(VaultFieldDefinition(
        key="identity.current_status", category="identity", data_type="string",
        validation_schema={"type": "string"}, cardinality="single",
        conflict_policy="latest_wins", sensitivity="normal", version=1, enabled=True,
    ))
    profile_db.commit()
    service = MemoryCandidateService(profile_db)
    first = service.propose(workspace, "vault_fact", key="identity.current_status",
                            proposed_value="Student", confidence=1, source_type="user_explicit",
                            allow_user_explicit=True)
    assert MemoryReconciler(profile_db).reconcile(first).accepted
    proposed = service.propose(workspace, "vault_fact", key="identity.current_status",
                               proposed_value="Graduate", confidence=1, source_type="document")
    result = MemoryReconciler(profile_db).reconcile(proposed)
    assert not result.accepted and proposed.status == "needs_review"
    issue = StudentRecordService(profile_db).issues(workspace)[0]
    assert issue.candidate_id == proposed.id

    resolved = ProfileIssueService(profile_db).resolve(
        workspace, issue.id, "accept_proposed", note="The document is correct",
    )
    profile_db.commit()
    assert resolved["resolved"] is True
    assert proposed.status == "superseded" and issue.status == "resolved"
    assert StudentSnapshotService(profile_db).build(workspace).fact_value(
        "identity.current_status") == "Graduate"


def test_provide_new_value_reconciles_before_issue_closes(profile_db):
    workspace = profile_db.info["workspace"]
    profile_db.add(VaultFieldDefinition(
        key="identity.current_status", category="identity", data_type="string",
        validation_schema={"type": "string"}, cardinality="single",
        conflict_policy="latest_wins", sensitivity="normal", version=1, enabled=True,
    ))
    profile_db.commit()
    candidates = MemoryCandidateService(profile_db)
    current = candidates.propose(
        workspace, "vault_fact", key="identity.current_status",
        proposed_value="Student", confidence=1, source_type="user_explicit",
        allow_user_explicit=True,
    )
    assert MemoryReconciler(profile_db).reconcile(current).accepted
    conflicting = candidates.propose(
        workspace, "vault_fact", key="identity.current_status",
        proposed_value="Graduate", confidence=1, source_type="agent",
    )
    assert MemoryReconciler(profile_db).reconcile(conflicting).reason == "needs_review"
    issue = StudentRecordService(profile_db).issues(workspace)[0]

    resolved = ProfileIssueService(profile_db).resolve(
        workspace, issue.id, "provide_new", value="Working professional",
    )
    profile_db.commit()
    assert resolved["resolved"] is True
    assert issue.status == "resolved"
    assert StudentSnapshotService(profile_db).build(workspace).fact_value(
        "identity.current_status") == "Working professional"


def test_agent_record_conflict_is_linked_without_overwrite(profile_db):
    workspace = profile_db.info["workspace"]
    candidates = MemoryCandidateService(profile_db)
    original = candidates.propose(
        workspace, "student_record", key="education",
        proposed_value={
            "qualification_name": "BS Computer Science",
            "institution_name": "Example University",
            "result": {"gpa": 3.4, "gpa_scale": 4},
        },
        confidence=1, source_type="user_explicit", allow_user_explicit=True,
    )
    accepted = MemoryReconciler(profile_db).reconcile(original)
    conflicting = candidates.propose(
        workspace, "student_record", key="education",
        proposed_value={
            "qualification_name": "BS Computer Science",
            "institution_name": "Example University",
            "result": {"gpa": 3.9, "gpa_scale": 4},
        },
        confidence=1, source_type="agent",
    )
    result = MemoryReconciler(profile_db).reconcile(conflicting)

    assert result.reason == "needs_review"
    issue = StudentRecordService(profile_db).issues(workspace)[0]
    assert issue.candidate_id == conflicting.id
    education = StudentRecordService(profile_db).get(workspace, "education", accepted.result_id)
    assert education.result["gpa"] == 3.4


def test_ambiguous_record_issue_can_accept_as_separate_record(profile_db):
    workspace = profile_db.info["workspace"]
    _education(profile_db, qualification_name="BS Computer Science",
               institution_name="University A")
    _education(profile_db, qualification_name="BS Computer Science",
               institution_name="University B")
    candidates = MemoryCandidateService(profile_db)
    ambiguous = candidates.propose(
        workspace, "student_record", key="education",
        proposed_value={"qualification_name": "BS Computer Science",
                        "result": {"gpa": 3.5, "gpa_scale": 4}},
        confidence=1, source_type="agent",
    )
    assert MemoryReconciler(profile_db).reconcile(ambiguous).reason == "needs_review"
    issue = StudentRecordService(profile_db).issues(workspace)[0]
    assert len(issue.evidence["matching_record_ids"]) == 2

    resolved = ProfileIssueService(profile_db).resolve(
        workspace, issue.id, "accept_proposed",
    )
    profile_db.commit()
    assert resolved["resolved"] is True
    rows = StudentRecordService(profile_db).list(workspace, "education")
    assert len(rows) == 3
    assert sum(row.result == {"gpa": 3.5, "gpa_scale": 4} for row in rows) == 1


def test_safe_preference_keeps_configured_latest_wins(profile_db):
    workspace = profile_db.info["workspace"]
    profile_db.add(VaultFieldDefinition(
        key="preferences.study_mode", category="preferences", data_type="string",
        validation_schema={"type": "string"}, cardinality="single",
        conflict_policy="latest_wins", sensitivity="normal", version=1, enabled=True,
    ))
    profile_db.commit()
    candidates = MemoryCandidateService(profile_db)
    first = candidates.propose(
        workspace, "vault_fact", key="preferences.study_mode",
        proposed_value="online", confidence=1, source_type="conversation",
    )
    second = candidates.propose(
        workspace, "vault_fact", key="preferences.study_mode",
        proposed_value="hybrid", confidence=1, source_type="agent",
    )
    assert MemoryReconciler(profile_db).reconcile(first).accepted
    assert MemoryReconciler(profile_db).reconcile(second).accepted
    assert not StudentRecordService(profile_db).issues(workspace)
    assert StudentSnapshotService(profile_db).build(workspace).fact_value(
        "preferences.study_mode") == "hybrid"


def test_profile_answer_saves_synchronously_and_recalculates(profile_db, monkeypatch):
    workspace = profile_db.info["workspace"]
    profile_db.add(VaultFieldDefinition(
        key="location.current_country", category="location", data_type="string",
        validation_schema={"type": "string"}, cardinality="single",
        conflict_policy="latest_wins", sensitivity="normal", version=1, enabled=True,
    ))
    _requirement(profile_db, "location.current_country", "critical")
    profile_db.commit()
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "all")
    context = ToolContext(
        workspace_id=workspace, agent_name=pai.PAI_AGENT_NAME, api=None,
        user_id=profile_db.info["user"],
    )
    result = asyncio.run(answer_profile_requirement(context, {
        "requirement_key": "location.current_country", "answer": "Pakistan",
    }))
    assert result["ok"] is True
    assert result["data"]["completion"]["personalizedCounselingEligible"] is True
    assert StudentSnapshotService(profile_db).build(workspace).fact_value(
        "location.current_country") == "Pakistan"


def test_rollout_and_collection_tool_boundary(profile_db, monkeypatch):
    workspace = profile_db.info["workspace"]
    _requirement(profile_db, "missing.critical", "critical")
    profile_db.commit()

    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "shadow")
    assert ProfileCompletionService(profile_db).evaluate(workspace)["counselorMode"] == "normal"
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "all")
    assert ProfileCompletionService(profile_db).evaluate(workspace)["counselorMode"] == "collection"
    assert "operator.delegate" not in pai.allowed_tools_for_mode("collection")
    assert not ({
        "memory.context", "vault.get", "memory.search", "memory.episodes",
        "memory.remember", "memory.forget",
    } & pai.allowed_tools_for_mode("collection"))
    assert "profile.answer" in pai.allowed_tools_for_mode("collection")
    assert "profile.answer" not in pai.allowed_tools_for_mode("normal")

    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "new")
    monkeypatch.setattr(
        config, "PAI_PROFILE_COMPLETION_ROLLOUT_AT",
        (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    assert ProfileCompletionService(profile_db).evaluate(workspace)["enforced"] is True


def test_new_rollout_rejects_invalid_cutoff(profile_db, monkeypatch):
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "new")
    monkeypatch.setattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_AT", "not-a-date")
    with pytest.raises(RuntimeError, match="ISO-8601"):
        config.validate_startup()
