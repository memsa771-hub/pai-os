from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

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
    ))
    state = CounselingEvaluator().derive(
        message="I uploaded my CV", vault_context=view, journey=None,
        completion={"personalizedCounselingEligible": False},
    )
    assert view["identity"]["full_name"]["value"] == "A Student"
    assert state.next_move.value == "REFLECT"
    assert state.focus == "use uploaded evidence"


def test_operator_intake_only_proposes_quoted_claims():
    from app.services.operator import consume_student_understanding_delta

    proposed = []
    jobs = []

    class Candidates:
        def __init__(self, db):
            pass

        def propose(self, **kwargs):
            proposed.append(kwargs)
            return SimpleNamespace(id=str(len(proposed)))

    class Fields:
        def __init__(self, db):
            pass

        def get(self, key):
            return object() if key == "preferences.target_countries" else None

    class Jobs:
        def __init__(self, db):
            pass

        def enqueue(self, *args, **kwargs):
            jobs.append((args, kwargs))

    delta = {"facts": [
        {"key": "preferences.target_countries", "value": ["Germany"],
         "evidence": {"quote": "Germany"}},
        {"key": "preferences.target_countries", "value": ["Canada"],
         "evidence": {"quote": "Canada"}},
    ], "records": [{"type": "education", "data": {"qualification_name": "BS CS"},
                   "evidence": {"quote": "BS CS"}}]}
    with patch("app.memory.candidates.MemoryCandidateService", Candidates), \
         patch("app.memory.field_definitions.VaultFieldDefinitionService", Fields), \
         patch("app.jobs.service.BackgroundJobService", Jobs):
        count = consume_student_understanding_delta(
            object(), "student", delta, source_event_id="event", source_text="BS CS in Germany")
        conflicting = consume_student_understanding_delta(
            object(), "student", {**delta, "conflicts": [{"field": "education"}]},
            source_text="BS CS in Germany")
    assert count == 2 and len(jobs) == 1
    assert conflicting == 0
    assert [item["candidate_type"] for item in proposed] == ["vault_fact", "student_record"]
    assert all(item["source_type"] == "conversation" for item in proposed)


def test_same_turn_current_education_conflict_is_clarified():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("BS CS", "bachelor", academic_status="current")],
    }))
    assert same_turn_education_conflict(view, "I already completed a PhD in BBA")
    assert same_turn_education_conflict(view, "Actually, I already completed a PhD") is None


def test_baseline_sufficiency_depends_on_situation():
    builder = StudentUnderstandingBuilder()
    records = {"education": [_education("BS CS", "bachelor", result={"gpa": 3.2})],
               "goal": [{"id": "g1", "goal_type": "study_abroad", "title": "Master's",
                         "details": {"motivation": "AI career", "target_countries": ["Germany"]}}]}
    view = builder.build("student", snapshot=_snapshot(records))
    assert not baseline_sufficient(view)
    assert any(gap.get("focus") == "budget" for gap in view["open_gaps"])
    budget = {"finance.budget": {"value": {"amount": 12000, "currency": "EUR"},
                                 "source_type": "user_explicit"}}
    view = builder.build("student", snapshot=_snapshot(records, budget))
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


def test_early_student_experience_is_not_required():
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot({
        "education": [_education("Secondary school", "secondary", academic_status="current")],
    }))
    assert view["domain_status"]["experience"] == "NOT_APPLICABLE"
    assert view["domain_status"]["projects"] == "NOT_APPLICABLE"


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
    test_early_student_experience_is_not_required()
    print("11 understanding tests passed")
