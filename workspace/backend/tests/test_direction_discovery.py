"""School-stage discovery remains evidence-led and uses existing OS boundaries."""

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.counseling.evaluator import CounselingEvaluator
from app.counseling.policy import CounselingPolicy
from app.counseling.understanding import StudentUnderstandingBuilder, baseline_sufficient, student_mirror
from app.journey import JourneyCoordinator, JourneyService
from app.journey.direction_discovery import DirectionDiscoveryService
from app.memory.errors import MemoryDataError
from app.memory.candidates import MemoryCandidateService
from app.memory.reconciler import MemoryReconciler
from app.memory.student_context_gateway import StudentContextGateway, StudentContextAccessDenied
from app.memory.student_records import StudentRecordService
from app.memory.student_schema import validate_record
from app.memory.student_snapshot import StudentSnapshot
from app.models import ExecutionRun
from scripts.counselor_eval_support import StudentSession


def _apply(records, workspace_id, kind, values, *, source="conversation", quote=None,
           execution_run_id=None, **kwargs):
    return records.apply(
        workspace_id, kind, values, source_type=source,
        claim_origin="student" if source in {"conversation", "user_explicit"} else "system",
        capture_method="conversation" if source == "conversation" else "service",
        evidence=({**({"quote": quote} if quote else {}),
                   **({"execution_run_id": execution_run_id} if execution_run_id else {})}
                  or None), **kwargs,
    )


def _completed_run(db, workspace_id, domain, activity_type, title):
    run = ExecutionRun(
        workspace_id=workspace_id, requested_by="openagents:pai",
        objective=f"Complete {title}", status="completed",
        result={"exploration_completed": {
            "domain": domain, "activity_type": activity_type,
            "title": title, "completed": True,
        }},
    )
    db.add(run)
    db.flush()
    return run.id


def test_undecided_student_has_a_useful_mirror_without_forced_direction():
    with StudentSession() as student, student.factory() as db:
        records = StudentRecordService(db)
        _apply(records, student.workspace_id, "education", {
            "qualification_name": "A Levels", "canonical_level": "upper_secondary",
            "academic_status": "current", "result": {"grade": "Math A"},
        }, quote="I am doing A Levels and got Math A")
        _apply(records, student.workspace_id, "exploration_experience", {
            "domain": "computer_science", "activity_type": "field_exposure",
            "title": "Considering coding", "activity_status": "planned",
            "exposure_level": "none",
        }, quote="I have not tried coding yet")
        db.commit()
        view = StudentUnderstandingBuilder(db).build(student.workspace_id,
            discovery={"current_direction": {"status": "UNKNOWN", "source_event_id": "student"}})
        mirror = student_mirror(view)
        assert baseline_sufficient(view)
        assert view["goals"]["nodes"] == []
        assert view["exposure"]["domains"][0]["reported_level"] == "none"
        assert "reported exposure none" in mirror
        assert "not a verdict" in mirror
        assert JourneyService(db).list(student.workspace_id) == []


def test_stated_interest_with_no_exposure_is_not_treated_as_proven_fit():
    snapshot = StudentSnapshot("student", {
        "career.primary_interest": {"value": "Computer Science", "source_type": "user_explicit"},
    }, {
        "education": [{"id": "school", "qualification_name": "A Levels",
                       "canonical_level": "upper_secondary", "result": {"grade": "A"}}],
        "exploration_experience": [{"id": "untried", "domain": "computer_science",
            "activity_type": "field_exposure", "title": "Coding",
            "activity_status": "planned", "exposure_level": "none"}],
    }, (), datetime.now(timezone.utc))
    view = StudentUnderstandingBuilder().build("student", snapshot=snapshot,
        field_definitions={"career.primary_interest": SimpleNamespace(
            sensitivity="normal", context_tags=[])},
        baseline={"status": "confirmed", "version": 1})
    assert len(view["interests"]["stated"]) == 1
    assert view["interests"]["experienced"] == []
    assert view["exposure"]["domains"][0]["reported_level"] == "none"
    state = CounselingEvaluator().derive(message="I think I like Computer Science",
        vault_context=view, journey=None, completion={"personalizedCounselingEligible": True})
    policy = CounselingPolicy().decide(state)
    assert state.next_move.value == "COUNSEL"
    assert policy.personalized_advice_allowed and not policy.operator_allowed
    assert "fit" not in student_mirror(view).casefold()


def test_exploration_timestamps_have_order_and_timezone():
    values = {"domain": "computer_science", "activity_type": "mini_project",
              "title": "Python puzzle", "activity_status": "completed",
              "started_at": "2026-10-01T09:00:00Z",
              "completed_at": "2026-10-01T10:00:00Z"}
    assert validate_record("exploration_experience", values)["completed_at"] == values["completed_at"]
    with pytest.raises(MemoryDataError, match="must not precede"):
        validate_record("exploration_experience", {**values,
            "completed_at": "2026-10-01T08:00:00Z"})
    with pytest.raises(MemoryDataError, match="timezone-aware"):
        validate_record("exploration_experience", {**values,
            "started_at": "2026-10-01T09:00:00"})


def test_reflection_requires_student_evidence_and_refs_are_workspace_scoped():
    with StudentSession() as student, student.factory() as db:
        records = StudentRecordService(db)
        base = {"domain": "computer_science", "activity_type": "mini_project",
                "title": "Python exercise", "activity_status": "completed"}
        with pytest.raises(MemoryDataError, match="student-sourced"):
            _apply(records, student.workspace_id, "exploration_experience",
                   {**base, "student_reflection": {"enjoyed": True}}, source="agent")
        with pytest.raises(MemoryDataError, match="student quote"):
            _apply(records, student.workspace_id, "exploration_experience",
                   {**base, "student_reflection": {"enjoyed": True}})
        with pytest.raises(MemoryDataError, match="durable execution evidence"):
            _apply(records, student.workspace_id, "exploration_experience", base,
                   source="system")
        with pytest.raises(MemoryDataError, match="does not verify"):
            _apply(records, student.workspace_id, "exploration_experience", base,
                   source="system", execution_run_id="nonexistent")
        with pytest.raises(MemoryDataError, match="this student's active record"):
            _apply(records, student.workspace_id, "exploration_experience",
                   {**base, "evidence_refs": [{"kind": "project", "id": "another-student"}]},
                   source="system")
        project = _apply(records, student.workspace_id, "project", {"name": "Python puzzle"},
                         quote="I built a Python puzzle")
        experience = _apply(records, student.workspace_id, "exploration_experience", {
            **base, "student_reflection": {"enjoyed": True,
                                           "what_enjoyed": "debugging"},
            "evidence_refs": [{"kind": "project", "id": project.id}],
        }, quote="I enjoyed debugging the Python puzzle")
        db.commit()
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert view["interests"]["experienced"][0]["basis"] == "student_reflection"
        assert view["explorations"]["nodes"][0]["id"] == experience.id
        assert {"from": {"type": "exploration_experience", "id": experience.id},
                "to": {"type": "project", "id": project.id}, "relation": "has_evidence",
                "basis": "canonical_reference"} in view["relationships"]
        assert "debugging" in student_mirror(view)


def test_direction_journey_tracks_multiple_options_and_canonical_completion():
    with StudentSession() as student, student.factory() as db:
        coordinator = JourneyCoordinator(JourneyService(db))
        assert coordinator.observe_message(student.workspace_id,
            "I don't know what I should study after school") is None
        journey = coordinator.observe_message(student.workspace_id,
            "I want to explore possible study directions")
        assert journey.journey_type == "direction_discovery"
        discovery = DirectionDiscoveryService(db)
        journey = discovery.start(student.workspace_id, ["Computer Science", "Economics"])
        assert len([goal for goal in journey.goals if goal["type"] == "subgoal"]) == 2
        assert len(journey.milestones) == 4
        records = StudentRecordService(db)
        experience = _apply(records, student.workspace_id, "exploration_experience", {
            "domain": "computer_science", "activity_type": "mini_project",
            "title": "Beginner Python puzzle", "activity_status": "planned",
        }, source="system")
        assert discovery.observe_exploration(student.workspace_id, experience.id) == []
        run_id = _completed_run(db, student.workspace_id, "computer_science",
                                "mini_project", "Beginner Python puzzle")
        _apply(records, student.workspace_id, "exploration_experience", {
            "activity_status": "completed",
        }, source="system", execution_run_id=run_id, record_id=experience.id)
        changed = discovery.observe_exploration(student.workspace_id, experience.id)
        assert len(changed) == 1
        journey = JourneyService(db).get(student.workspace_id, journey.id)
        assert len([m for m in journey.milestones if m["status"] == "completed"]) == 1
        assert discovery.observe_exploration(student.workspace_id, experience.id) == []
        _apply(records, student.workspace_id, "exploration_experience", {
            "student_reflection": {"enjoyed": True, "wants_more_exposure": True},
        }, record_id=experience.id, quote="I enjoyed it and want to try more")
        changed = discovery.observe_exploration(student.workspace_id, experience.id)
        assert len(changed) == 1
        db.commit()
        journey = JourneyService(db).get(student.workspace_id, journey.id)
        assert len([m for m in journey.milestones if m["status"] == "completed"]) == 2
        assert any(m["status"] == "pending" for m in journey.milestones)
        assert next(goal for goal in journey.goals if goal.get("domain") == "computer_science")["status"] == "completed"
        economics = _apply(records, student.workspace_id, "exploration_experience", {
            "domain": "economics", "activity_type": "data_exercise",
            "title": "Simple market dataset", "activity_status": "completed",
            "student_reflection": {"enjoyed": False, "what_disliked": "the charting"},
        }, quote="I tried the market dataset but did not enjoy the charting")
        assert len(discovery.observe_exploration(student.workspace_id, economics.id)) == 2
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert view["exposure"]["domains"][0]["completed_count"] == 1
        assert view["interests"]["experienced"][0]["domain"] == "computer_science"
        finished = JourneyService(db).get(student.workspace_id, journey.id)
        assert finished.current_stage == "REVIEWING"
        assert finished.next_recommended_action["type"] == "compare_directions"
        assert {goal["status"] for goal in finished.goals if goal["type"] == "subgoal"} == {"completed"}


def test_reconciled_completion_advances_journey_without_claiming_reflection():
    with StudentSession() as student, student.factory() as db:
        discovery = DirectionDiscoveryService(db)
        journey = discovery.start(student.workspace_id, ["Computer Science"])
        run_id = _completed_run(db, student.workspace_id, "computer_science",
                                "mini_project", "Coding activity")
        candidate = MemoryCandidateService(db).propose(
            student.workspace_id, "student_record", key="exploration_experience",
            proposed_value={"domain": "computer_science", "activity_type": "mini_project",
                            "title": "Coding activity", "activity_status": "completed"},
            source_type="system", confidence=1.0,
            evidence={"execution_run_id": run_id},
        )
        result = MemoryReconciler(db).reconcile(candidate)
        db.commit()
        assert result.accepted
        refreshed = JourneyService(db).get(student.workspace_id, journey.id)
        assert [item["step"] for item in refreshed.milestones
                if item["status"] == "completed"] == ["activity"]
        assert refreshed.next_recommended_action["step"] == "reflection"


def test_exploration_scope_is_separate_from_other_student_context():
    with StudentSession() as student, student.factory() as db:
        records = StudentRecordService(db)
        _apply(records, student.workspace_id, "project", {"name": "Private project"},
               quote="I made a private project")
        _apply(records, student.workspace_id, "exploration_experience", {
            "domain": "economics", "activity_type": "field_exposure",
            "title": "Market exercise", "activity_status": "planned",
        }, quote="I will try a market exercise")
        gateway = StudentContextGateway(db)
        with pytest.raises(StudentContextAccessDenied):
            gateway.get(student.workspace_id, ["exploration"], caller="unlisted")
        context = gateway.get(student.workspace_id, ["exploration"],
                              caller="pai-operator", granted_permissions={"vault.exploration.read"})
        assert set(context.domains) == {"exploration"}
        assert set(context.domains["exploration"]["records"]) == {"exploration_experience"}
        assert context.domains["exploration"]["records"]["exploration_experience"][0]["title"] == "Market exercise"


def test_club_and_volunteering_are_activities_without_implied_awards():
    with StudentSession() as student, student.factory() as db:
        records = StudentRecordService(db)
        _apply(records, student.workspace_id, "activity", {
            "title": "Debate club", "activity_type": "club", "role": "Member",
            "details": {"skills": ["Public speaking"]},
        }, quote="I am a member of debate club")
        _apply(records, student.workspace_id, "activity", {
            "title": "Food bank shifts", "activity_type": "volunteering",
        }, quote="I volunteered at the food bank")
        _apply(records, student.workspace_id, "skill", {"name": "Public speaking"},
               quote="I practiced public speaking")
        db.commit()
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert len(view["activities"]["nodes"]) == 2
        assert view["achievements"]["nodes"] == []
        assert any(edge["from"]["type"] == "activity" and
                   edge["to"]["type"] == "skill" for edge in view["relationships"])
        assert "Debate club" in student_mirror(view)
        context = StudentContextGateway(db).get(student.workspace_id, ["activities"],
            caller="pai-operator", granted_permissions={"vault.activities.read"})
        assert set(context.domains["activities"]["records"]) == {"activity"}


def test_rejected_direction_keeps_record_revision_and_is_not_current_interest():
    with StudentSession() as student, student.factory() as db:
        records = StudentRecordService(db)
        goal = _apply(records, student.workspace_id, "goal", {
            "goal_type": "degree_direction", "title": "Computer Science",
            "commitment": "considering", "details": {"field_of_study": "Computer Science"},
        }, quote="I am considering Computer Science")
        _apply(records, student.workspace_id, "goal", {
            "details": {"direction_status": "rejected",
                        "decision_rationale": "I prefer the economics work"},
        }, record_id=goal.id, source="user_explicit",
            quote="I reject CS because I prefer economics")
        _apply(records, student.workspace_id, "goal", {
            "goal_type": "degree_direction", "title": "Economics",
            "commitment": "considering", "details": {"field_of_study": "Economics"},
        }, quote="I am considering Economics")
        db.commit()
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert len(view["goals"]["nodes"]) == 2
        assert [item["domain"] for item in view["interests"]["stated"]] == ["economics"]
        assert len(records.history(student.workspace_id, "goal", goal.id)) == 2
