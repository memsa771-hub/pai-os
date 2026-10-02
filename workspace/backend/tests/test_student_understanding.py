from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
import asyncio
import json
from unittest.mock import patch

from sqlalchemy import select

from app.counseling.baseline import changed_domains, confirmed, metadata, save
from app.counseling.evaluator import CounselingEvaluator
from app.counseling.policy import CounselingPolicy
from app.counseling.turn_contract import parse_turn
from app.counseling.understanding import (StudentUnderstandingBuilder, baseline_sufficient,
                                         same_turn_education_conflict, student_mirror)
from app.memory.student_snapshot import StudentSnapshot


def _snapshot(records=None, facts=None):
    return StudentSnapshot("student", facts or {}, records or {}, (), datetime.now(timezone.utc))


def _education(name, level, **extra):
    return {"id": name, "qualification_name": name, "canonical_level": level,
            "source_type": "document", "verification_status": "document_supported", **extra}


def test_global_education_gaps_and_multiple_records():
    snapshot = _snapshot({"education": [
        _education("BS Computer Science", "bachelor", result={"gpa": 3.2, "gpa_scale": 4}),
        _education("MS Data Science", "master"),
    ]})
    view = StudentUnderstandingBuilder().build("student", snapshot=snapshot)
    assert len(view["education"]["nodes"]) == 2
    assert view["education"]["gaps"] == [{"level": "upper_secondary", "status": "UNKNOWN",
                                            "reason": "predecessor of bachelor is not recorded"}]
    assert "FSc" not in str(view) and "A Levels" not in str(view)
    assert view["education"]["nodes"][0]["provenance"]["verification"] == "document_supported"


def test_multiple_work_records_and_skill_links_are_derived():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "work_experience": [
            {"id": "w1", "role": "Intern", "organization": "A", "details": {"skills": ["Python"]}},
            {"id": "w2", "role": "Engineer", "organization": "B", "details": {"skills": ["SQL"]}},
        ],
        "project": [{"id": "p1", "name": "ML", "details": {"skills": ["Python"]}}],
        "skill": [{"id": "s1", "name": "Python", "source_type": "user_explicit"}],
    }))
    assert len(view["experience"]["nodes"]) == 2
    assert view["skills"]["nodes"][0]["supported_by"] == [
        {"type": "project", "id": "p1"}, {"type": "work_experience", "id": "w1"},
    ]


def test_mirror_and_confirmation_metadata_invalidate_only_changed_domain():
    builder = StudentUnderstandingBuilder()
    base = _snapshot({"education": [_education("BA", "bachelor")],
                      "goal": [{"id": "g1", "title": "Master's abroad",
                                "details": {"motivation": "career growth"}}]})
    view = builder.build("student", snapshot=base)
    assert "BA" in student_mirror(view)
    workspace = SimpleNamespace(settings={})
    save(workspace, status="mirror_review", view=view)
    assert confirmed("Yes, that's accurate.")
    saved = save(workspace, status="confirmed", view=view)
    assert saved["version"] == 1 and saved["confirmed_at"]
    assert not changed_domains(view, metadata(workspace))
    updated = builder.build("student", snapshot=_snapshot({
        **base.records, "skill": [{"id": "s1", "name": "Python"}],
    }))
    assert changed_domains(updated, metadata(workspace)) == ["skills"]
    affected_mirror = student_mirror(updated, ["skills"])
    assert "Python" in affected_mirror and "BA" not in affected_mirror


def test_turn_contract_keeps_state_internal():
    response, state = parse_turn('{"response":"What is your degree?",'
                                 '"counselor_state":{"student_understanding_delta":'
                                 '{"facts":[{"key":"goal.direction","value":"AI"}]},'
                                 '"next_move":{"type":"ASK","focus":"education"}}}')
    assert response == "What is your degree?"
    assert state["student_understanding_delta"]["records"] == []
    assert parse_turn("A plain reply") == ("A plain reply", {})


def test_discovery_keeps_context_but_locks_guidance():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor")],
    }))
    state = CounselingEvaluator().derive(
        message="Which master's fits me?", vault_context=view, journey={"current_stage": "planning"},
        completion={"personalizedCounselingEligible": True},
    )
    policy = CounselingPolicy().decide(state)
    assert view["education"]["nodes"][0]["qualification_name"] == "BS CS"
    assert not policy.personalized_advice_allowed
    assert not policy.operator_allowed
    assert policy.max_questions == 1


def test_onboarding_identity_and_uploaded_document_are_reused():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot(
        {"document": [{"id": "cv", "document_type": "cv", "title": "My CV"}]},
        {"identity.full_name": {"value": "A Student", "source_type": "user_explicit"}},
    ), field_definitions={"identity.full_name": SimpleNamespace(
        sensitivity="normal", context_tags=[])})
    state = CounselingEvaluator().derive(
        message="I uploaded my CV", vault_context=view, journey=None,
        completion={"personalizedCounselingEligible": False},
    )
    assert view["identity"]["full_name"]["value"] == "A Student"
    assert state.next_move.value == "REFLECT"
    assert state.focus == "use uploaded evidence"


def test_operator_intake_only_proposes_quoted_claims():
    from app.services.operator import consume_student_understanding_delta
    from app.models import EventRecord, MemoryCandidate
    from app.memory.reconciler import MemoryReconciler
    from app.memory.student_snapshot import StudentSnapshotService
    from scripts.counselor_eval_support import StudentSession

    delta = {"facts": [
        {"key": "preferences.target_countries", "value": ["Germany"],
         "evidence": {"quote": "Germany"}},
        {"key": "preferences.target_countries", "value": ["Canada"],
         "evidence": {"quote": "Canada"}},
    ], "records": [{"type": "education", "data": {"qualification_name": "BS CS"},
                   "evidence": {"quote": "BS CS"}}]}
    with StudentSession() as student, student.factory() as db:
        event_id = str(uuid.uuid4())
        db.add(EventRecord(id=event_id, network_id=student.workspace_id,
                           type="workspace.message.posted", source=f"human:{student.user_id}",
                           target="channel/pai-counselor", payload={"content": "BS CS in Germany"},
                           timestamp=student.next_timestamp()))
        db.commit()
        count = consume_student_understanding_delta(
            db, student.workspace_id, delta, source_event_id=event_id,
            source_text="BS CS in Canada")
        conflicting = consume_student_understanding_delta(
            db, student.workspace_id, {**delta, "conflicts": [{"field": "education"}]},
            source_event_id=event_id)
        db.commit()
        proposals = db.execute(select(MemoryCandidate).where(
            MemoryCandidate.workspace_id == student.workspace_id)).scalars().all()
        assert count == 2 and conflicting == 0
        assert {item.candidate_type for item in proposals} == {"vault_fact", "student_record"}
        assert all(item.source_type == "conversation" for item in proposals)
        outcomes = [MemoryReconciler(db).reconcile(item) for item in proposals]
        db.commit()
        assert all(outcome.accepted for outcome in outcomes)
        snapshot = StudentSnapshotService(db).build(student.workspace_id)
        assert snapshot.fact_value("preferences.target_countries") == ["Germany"]
        assert snapshot.records["education"][0]["qualification_name"] == "BS CS"


def test_same_turn_current_education_conflict_is_clarified():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", academic_status="current")],
    }))
    assert same_turn_education_conflict(view, "I already completed a PhD in BBA")
    assert same_turn_education_conflict(view, "Actually, I already completed a PhD") is None


def test_baseline_sufficiency_depends_on_situation():
    builder = StudentUnderstandingBuilder()
    records = {"education": [_education("BS CS", "bachelor", result={"gpa": 3.2})],
               "skill": [{"id": "s1", "name": "Python"}],
               "goal": [{"id": "g1", "goal_type": "study_abroad", "title": "Master's",
                         "details": {"motivation": "AI career", "target_countries": ["Germany"]}}]}
    discovery = {"education_history": {"status": "UNKNOWN", "source_event_id": "event"}}
    view = builder.build("student", snapshot=_snapshot(records), discovery=discovery)
    assert not baseline_sufficient(view)
    assert any(gap.get("focus") == "budget" for gap in view["open_gaps"])
    budget = {"finance.budget": {"value": {"amount": 12000, "currency": "EUR"},
                                 "source_type": "user_explicit"}}
    view = builder.build("student", snapshot=_snapshot(records, budget), discovery=discovery,
                         field_definitions={"finance.budget": SimpleNamespace(
                             sensitivity="normal", context_tags=[])})
    assert baseline_sufficient(view)


def test_confirmed_baseline_unlocks_counseling_and_delegation():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", result={"gpa": 3.2})],
        "goal": [{"id": "g", "title": "Master's", "details": {"motivation": "career"}}],
    }), baseline={"status": "confirmed", "version": 1})
    state = CounselingEvaluator().derive(
        message="Please research a shortlist", vault_context=view, journey=None,
        completion={"personalizedCounselingEligible": True},
    )
    policy = CounselingPolicy().decide(state)
    assert state.phase.value == "COUNSELING"
    assert policy.personalized_advice_allowed and policy.operator_allowed


def test_early_student_experience_is_not_inferred_inapplicable():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("Secondary school", "secondary", academic_status="current")],
    }))
    assert view["domain_status"]["experience"] == "UNKNOWN"
    assert view["domain_status"]["projects"] == "UNKNOWN"


def test_education_edges_and_courses_are_derived_without_inventing_qualifications():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [
            _education("BS CS", "bachelor", id="edu-bs", start_date="2022"),
            _education("A Levels", "upper_secondary", id="edu-a", end_date="2022"),
            _education("O Levels", "secondary", id="edu-o", end_date="2020"),
        ],
        "course": [
            {"id": "course-ml", "education_id": "edu-bs", "name": "Machine Learning"},
            {"id": "course-math", "education_id": "edu-a", "name": "Mathematics"},
        ],
    }))
    assert [(edge["from"], edge["to"]) for edge in view["education"]["edges"]] == [
        ("edu-o", "edu-a"), ("edu-a", "edu-bs"),
    ]
    assert {node["qualification_name"]: node["course_ids"]
            for node in view["education"]["nodes"]}["BS CS"] == ["course-ml"]
    assert "Machine Learning" in student_mirror(view)
    assert "edu-bs" not in student_mirror(view)
    assert "FSc" not in str(view) and "Abitur" not in str(view)


def test_course_update_invalidates_only_education_mirror_domain():
    builder = StudentUnderstandingBuilder()
    records = {"education": [_education("BS CS", "bachelor", id="edu")],
               "course": [{"id": "course", "education_id": "edu", "name": "Algorithms"}]}
    before = builder.build("student", snapshot=_snapshot(records))
    workspace = SimpleNamespace(settings={})
    save(workspace, status="confirmed", view=before)
    records["course"].append({"id": "course-2", "education_id": "edu", "name": "Machine Learning"})
    after = builder.build("student", snapshot=_snapshot(records))
    assert changed_domains(after, metadata(workspace)) == ["education"]
    changed_mirror = student_mirror(after, ["education"])
    assert "Machine Learning" in changed_mirror and "edu" not in changed_mirror


def test_other_record_families_and_finance_are_projected_separately():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "language_proficiency": [{"id": "lang", "language": "German", "proficiency": "B1"}],
        "certification": [{"id": "cert", "name": "AWS Cloud Practitioner"}],
        "application": [{"id": "app", "institution_name": "University X"}],
        "financial_sponsor": [{"id": "sponsor", "sponsor_type": "family"}],
        "scholarship_application": [{"id": "award", "scholarship_name": "Award Y"}],
        "visa": [{"id": "visa", "country": "Germany"}],
        "document": [{"id": "doc", "file_id": "file-secret", "document_type": "cv"}],
    }))
    assert view["languages"]["nodes"][0]["language"] == "German"
    assert view["certifications"]["nodes"][0]["name"] == "AWS Cloud Practitioner"
    assert view["applications"]["nodes"][0]["institution_name"] == "University X"
    assert view["finance"]["sponsors"][0]["sponsor_type"] == "family"
    assert view["scholarships"]["nodes"][0]["scholarship_name"] == "Award Y"
    assert view["visa"]["nodes"][0]["country"] == "Germany"
    assert "file-secret" not in student_mirror(view)


def test_cross_domain_support_links_are_bounded_and_evidenced():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", id="edu", field_of_study="Computer Science")],
        "course": [{"id": "ml", "education_id": "edu", "name": "Machine Learning"}],
        "project": [{"id": "project", "name": "Automation", "details": {"skills": ["Python"]}}],
        "work_experience": [{"id": "work", "organization": "A", "role": "Intern",
                             "details": {"skills": ["FastAPI"]}}],
        "research": [{"id": "research", "title": "Language study", "details": {"methods": ["NLP"]}}],
        "skill": [{"id": "python", "name": "Python"}, {"id": "fastapi", "name": "FastAPI"},
                  {"id": "nlp", "name": "NLP"}, {"id": "ml-skill", "name": "Machine Learning"}],
        "goal": [{"id": "goal", "goal_type": "academic", "title": "MSc CS",
                  "details": {"field_of_study": "Computer Science", "motivation": "research"}}],
    }))
    relations = view["relationships"]
    assert {(edge["from"]["type"], edge["to"]["type"], edge["relation"])
            for edge in relations} >= {
        ("project", "skill", "supports"), ("work_experience", "skill", "supports"),
        ("research", "skill", "supports"), ("course", "skill", "supports"),
        ("education", "goal", "relevant_to"),
    }
    assert view["coverage"]["relationships"]["shown"] == len(relations)


def test_cs_education_has_derived_relevance_to_stated_ai_goal():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", id="degree",
                                 field_of_study="Computer Science")],
        "goal": [{"id": "goal", "goal_type": "academic", "title": "MSc AI"}],
    }))
    assert {"from": {"type": "education", "id": "degree"},
            "to": {"type": "goal", "id": "goal"}, "relation": "relevant_to",
            "basis": "related_field_family"} in view["relationships"]


def test_large_sections_report_coverage_without_unbounded_nodes():
    rows = [{"id": str(i), "organization": "Company", "role": f"Role {i}"}
            for i in range(30)]
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "work_experience": rows,
    }))
    assert len(view["experience"]["nodes"]) == 12
    assert view["coverage"]["experience"] == {"shown": 12, "total": 30, "truncated": True}
    assert "Showing 12 of 30" in student_mirror(view)


def test_school_and_undecided_students_do_not_need_work_or_budget():
    school = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("Secondary", "secondary", result={"percentage": 82})],
        "goal": [{"id": "g", "goal_type": "learning", "title": "Explore science",
                  "details": {"motivation": "curiosity"}}],
    }))
    assert baseline_sufficient(school)
    undecided = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("Secondary", "secondary", result={"percentage": 82})],
    }), discovery={"current_direction": {"status": "UNKNOWN", "source_event_id": "event"}})
    assert baseline_sufficient(undecided)


def test_career_change_requires_relevant_history_and_constraints():
    builder = StudentUnderstandingBuilder()
    records = {
        "education": [_education("BS CS", "bachelor")],
        "goal": [{"id": "g", "goal_type": "career_transition", "title": "Change career",
                  "details": {"motivation": "more meaningful work"}}],
        "skill": [{"id": "s", "name": "Python"}],
    }
    assert not baseline_sufficient(builder.build("student", snapshot=_snapshot(records)))
    records["work_experience"] = [{"id": "w", "organization": "A", "role": "Developer"}]
    discovery = {"practical_constraints": {"status": "NOT_APPLICABLE", "source_event_id": "event"}}
    view = builder.build("student", snapshot=_snapshot(records), discovery=discovery)
    assert baseline_sufficient(view)
    assert view["domain_status"]["constraints"] == "NOT_APPLICABLE"


def test_confirmed_advice_roadmap_and_operator_permissions_are_distinct():
    evaluator, policy = CounselingEvaluator(), CounselingPolicy()
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", result={"gpa": 3.2})],
        "goal": [{"id": "g", "title": "Career in software",
                  "details": {"motivation": "build products"}}],
    }), baseline={"status": "confirmed", "version": 1})
    def decision(message):
        return policy.decide(evaluator.derive(
            message=message, vault_context=view, journey=None,
            completion={"personalizedCounselingEligible": True}))
    advice = decision("What do you think fits me?")
    assert advice.personalized_advice_allowed and not advice.roadmap_allowed
    assert not advice.operator_allowed
    roadmap = decision("Build my roadmap")
    assert roadmap.personalized_advice_allowed and roadmap.roadmap_allowed
    assert not roadmap.operator_allowed
    delegation = decision("Please research a shortlist")
    assert delegation.operator_allowed and not delegation.roadmap_allowed


def test_same_turn_conflicts_use_current_history_but_allow_goals_and_corrections():
    current_master = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("MSc", "master", academic_status="current")],
    }))
    assert same_turn_education_conflict(current_master, "I'm currently in Bachelor's")
    assert same_turn_education_conflict(current_master, "I want to do a PhD") is None
    assert same_turn_education_conflict(current_master, "I completed my Bachelor's in 2022") is None
    assert same_turn_education_conflict(current_master, "Actually I completed my Bachelor's") is None
    no_record = StudentUnderstandingBuilder().build("student", snapshot=_snapshot())
    assert same_turn_education_conflict(
        no_record, "I'm currently in Bachelor's",
        [{"role": "user", "content": "I'm currently studying for a Master's"}])


def test_counselor_delta_does_not_directly_write_canonical_records():
    from app.config import config
    from app.counseling import runtime
    from app.models import EducationRecord, MemoryCandidate
    from scripts.counselor_eval_support import StudentSession

    async def run():
        with StudentSession() as student:
            async def model(**kwargs):
                return {"role": "assistant", "content": json.dumps({
                    "response": "I have your degree as BS CS. What would you like to pursue?",
                    "counselor_state": {
                        "phase": "DISCOVERING", "baseline_ready": False,
                        "next_move": {"type": "ASK", "focus": "current_direction"},
                        "student_understanding_delta": {"facts": [], "records": [{
                            "type": "education", "data": {"qualification_name": "BS CS"},
                            "evidence": {"quote": "BS CS"},
                        }], "memories": [], "conflicts": [], "unknowns": []},
                    },
                })}
            with patch.object(runtime, "chat_completion_tools", model), \
                 patch.object(config, "PAI_API_KEY", "test"), \
                 patch.object(config, "PAI_MEMORY_CONTEXT_ENABLED", False):
                await student.turn("I did BS CS")
            with student.factory() as db:
                assert db.execute(select(EducationRecord)).scalars().all() == []
                candidates = db.execute(select(MemoryCandidate).where(
                    MemoryCandidate.candidate_type == "student_record")).scalars().all()
                assert len(candidates) == 1 and candidates[0].status == "pending"
    asyncio.run(run())


def test_mirror_confirmation_unlocks_advice_and_revalidates_only_changed_domain():
    from app.config import config
    from app.counseling import runtime
    from app.counseling.baseline import metadata
    from app.memory.student_records import StudentRecordService
    from app.models import Workspace
    from scripts.counselor_eval_support import StudentSession

    async def run():
        with StudentSession() as student:
            with student.factory() as db:
                records = StudentRecordService(db)
                for kind, values in (
                    ("education", {"qualification_name": "BS CS", "canonical_level": "bachelor",
                                   "result": {"gpa": 3.2, "gpa_scale": 4.0}}),
                    ("goal", {"goal_type": "career", "title": "Build software",
                              "details": {"motivation": "I enjoy solving problems"}}),
                ):
                    records.apply(student.workspace_id, kind, values,
                                  source_type="user_explicit", claim_origin="student",
                                  capture_method="conversation")
                db.commit()

            received = []
            async def model(**kwargs):
                received.append(kwargs)
                return {"role": "assistant", "content": "A useful direction is to build on your CS work."}
            with patch.object(runtime, "chat_completion_tools", model), \
                 patch.object(config, "PAI_API_KEY", "test"), \
                 patch.object(config, "PAI_MEMORY_CONTEXT_ENABLED", False):
                await student.turn("continue")
                assert "Education so far" in student.transcript[-1]["content"]
                await student.turn("Yes, that's accurate.")
                with student.factory() as db:
                    assert metadata(db.get(Workspace, student.workspace_id))["status"] == "confirmed"
                await student.turn("What do you think fits me?")
                assert "personalized_advice_allowed=true" in received[-1]["system_prompt"]
                assert "operator__delegate" not in {
                    tool["function"]["name"] for tool in received[-1]["tools"]}
                await student.turn("Please research a shortlist")
                assert "operator__delegate" in {
                    tool["function"]["name"] for tool in received[-1]["tools"]}

                with student.factory() as db:
                    StudentRecordService(db).apply(
                        student.workspace_id, "skill", {"name": "Python"},
                        source_type="user_explicit", claim_origin="student",
                        capture_method="conversation")
                    db.commit()
                await student.turn("continue")
                changed_mirror = student.transcript[-1]["content"]
                assert "Python" in changed_mirror and "Education so far" not in changed_mirror
                await student.turn("Yes, that's accurate.")
                with student.factory() as db:
                    baseline = metadata(db.get(Workspace, student.workspace_id))
                    assert baseline["status"] == "confirmed" and baseline["version"] == 2
    asyncio.run(run())


if __name__ == "__main__":
    test_global_education_gaps_and_multiple_records()
    test_multiple_work_records_and_skill_links_are_derived()
    test_mirror_and_confirmation_metadata_invalidate_only_changed_domain()
    test_turn_contract_keeps_state_internal()
    test_discovery_keeps_context_but_locks_guidance()
    test_onboarding_identity_and_uploaded_document_are_reused()
    test_operator_intake_only_proposes_quoted_claims()
    test_same_turn_current_education_conflict_is_clarified()
    test_baseline_sufficiency_depends_on_situation()
    test_confirmed_baseline_unlocks_counseling_and_delegation()
    test_early_student_experience_is_not_inferred_inapplicable()
    test_education_edges_and_courses_are_derived_without_inventing_qualifications()
    test_course_update_invalidates_only_education_mirror_domain()
    test_other_record_families_and_finance_are_projected_separately()
    test_cross_domain_support_links_are_bounded_and_evidenced()
    test_cs_education_has_derived_relevance_to_stated_ai_goal()
    test_large_sections_report_coverage_without_unbounded_nodes()
    test_school_and_undecided_students_do_not_need_work_or_budget()
    test_career_change_requires_relevant_history_and_constraints()
    test_confirmed_advice_roadmap_and_operator_permissions_are_distinct()
    test_same_turn_conflicts_use_current_history_but_allow_goals_and_corrections()
    test_counselor_delta_does_not_directly_write_canonical_records()
    test_mirror_confirmation_unlocks_advice_and_revalidates_only_changed_domain()
    print("23 understanding tests passed")
