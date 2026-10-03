"""Own voice, outside influence, and decision readiness stay separate."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.counseling.baseline import save
from app.counseling.decision_sufficiency import (DecisionSufficiencyEvaluator,
    validated_decision_intent, guard_premature_verdict)
from app.counseling.evaluator import CounselingEvaluator
from app.counseling.policy import CounselingPolicy
from app.counseling.understanding import StudentUnderstandingBuilder, student_mirror
from app.memory.reconciler import MemoryReconciler
from app.memory.student_records import StudentRecordService
from app.memory.student_snapshot import StudentSnapshot
from app.models import EventRecord, MemoryCandidate, Workspace
from app.services.operator import consume_student_understanding_delta
from scripts.counselor_eval_support import StudentSession


def _student_turn(db, student, message, records=(), facts=()):
    records = [{**item, "attribution": item.get("attribution") or {
        "claim_owner": "external" if item.get("type") == "external_influence" else "student"}}
        for item in records]
    event_id = str(uuid4())
    db.add(EventRecord(
        id=event_id, network_id=student.workspace_id,
        type="workspace.message.posted", source=f"human:{student.user_id}",
        target="channel/pai-counselor", payload={"content": message},
        timestamp=student.next_timestamp(),
    ))
    db.commit()
    count = consume_student_understanding_delta(db, student.workspace_id,
        {"records": list(records), "facts": list(facts)}, source_event_id=event_id)
    pending = db.execute(select(MemoryCandidate).where(
        MemoryCandidate.workspace_id == student.workspace_id,
        MemoryCandidate.status == "pending",
    )).scalars().all()
    outcomes = [MemoryReconciler(db).reconcile(item) for item in pending]
    db.commit()
    return count, outcomes


def _influence(label, direction, quote, *, kind="parent", influence_type="career_suggestion",
               alignment_quote=None, **extra):
    return {"type": "external_influence", "data": {
        "influencer_type": kind, "source_label": label,
        "suggested_direction": direction, "influence_type": influence_type, **extra,
    }, "evidence": {"quote": quote}, "attribution": {
        "claim_owner": "external", **({"alignment_quote": alignment_quote} if alignment_quote else {})}}


def _snapshot(records=None, facts=None):
    return StudentSnapshot("student", facts or {}, records or {}, (), datetime.now(timezone.utc))


def test_father_suggestion_enters_influence_but_not_goal_or_interest():
    with StudentSession() as student, student.factory() as db:
        quote = "My father says CS"
        count, outcomes = _student_turn(db, student, quote, records=[
            _influence("father", "Computer Science", quote),
            {"type": "goal", "data": {"goal_type": "degree_direction",
                "title": "Computer Science"}, "evidence": {"quote": quote},
             "attribution": {"claim_owner": "external"}},
            {"type": "external_influence", "data": {
                "influencer_type": "parent", "source_label": "father",
                "suggested_direction": "Computer Science",
                "influence_type": "career_suggestion", "student_alignment": "aligned",
            }, "evidence": {"quote": quote}, "attribution": {"claim_owner": "external"}},
        ], facts=[{"key": "career.primary_interest", "value": "Computer Science",
                  "evidence": {"quote": quote}, "attribution": {"claim_owner": "external"}}])
        assert count == 1 and all(result.accepted for result in outcomes)
        profile = StudentRecordService(db).snapshot(student.workspace_id)
        assert profile["goal"] == []
        assert "student_alignment" not in profile["external_influence"][0]
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert view["influences"]["nodes"][0]["source_type"] == "parent"
        assert view["influences"]["nodes"][0]["student_alignment"] == "unknown"
        assert view["influences"]["nodes"][0]["student_quote"] == quote
        assert view["student_voice"]["current_direction"]["status"] == "unknown"
        assert "Outside influences" in student_mirror(view)


def test_own_preference_can_coexist_with_parent_influence_and_peer_path():
    with StudentSession() as student, student.factory() as db:
        both = "My father says CS and I also really want CS"
        count, _ = _student_turn(db, student, both, records=[
            _influence("father", "Computer Science", both,
                       student_alignment="aligned", alignment_quote="I also really want CS"),
            {"type": "goal", "data": {"goal_type": "degree_direction",
                "title": "Computer Science", "commitment": "considering"},
             "evidence": {"quote": "I also really want CS"}},
            {"type": "student_voice_statement", "data": {
                "voice_type": "direction", "statement": "I also really want CS",
                "direction": "Computer Science", "commitment": "considering"},
             "evidence": {"quote": "I also really want CS"}},
        ])
        assert count == 3, {kind: StudentRecordService(db).snapshot(student.workspace_id)[kind]
                            for kind in ("goal", "student_voice_statement", "external_influence")}
        friend = "My friend is doing BBA"
        count, _ = _student_turn(db, student, friend, records=[
            _influence("friend", "BBA", friend, kind="friend", influence_type="peer_path"),
            {"type": "goal", "data": {"goal_type": "degree_direction", "title": "BBA"},
             "evidence": {"quote": friend}, "attribution": {"claim_owner": "external"}},
        ])
        assert count == 1
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert [goal["title"] for goal in view["goals"]["nodes"]] == ["Computer Science"]
        assert {node["source_label"] for node in view["influences"]["nodes"]} == {"father", "friend"}
        assert view["student_voice"]["current_direction"]["status"] == "exploring"


def test_social_influence_uncertainty_and_correction_preserve_history():
    with StudentSession() as student, student.factory() as db:
        first = "My father says CS"
        _student_turn(db, student, first, records=[_influence("father", "Computer Science", first)])
        row = StudentRecordService(db).list(student.workspace_id, "external_influence")[0]
        update = "Actually, my father now says medicine instead"
        count, _ = _student_turn(db, student, update, records=[
            {**_influence("father", "Medicine", update),
             "entities": {"record_id": row.id},
             "attribution": {"claim_owner": "external", "correction": True,
                             "correction_quote": update}},
        ])
        assert count == 1
        social = "Everyone says medicine is prestigious"
        _student_turn(db, student, social, records=[
            _influence("everyone", "Medicine", social,
                       kind="social_expectation", influence_type="social_message"),
        ])
        unsure = "I don't know what I want"
        _student_turn(db, student, unsure, records=[{
            "type": "student_voice_statement", "data": {
                "voice_type": "uncertainty", "statement": unsure},
            "evidence": {"quote": unsure},
        }])
        records = StudentRecordService(db)
        assert len(records.list(student.workspace_id, "external_influence")) == 2
        assert len(records.history(student.workspace_id, "external_influence", row.id)) == 2
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert view["student_voice"]["current_direction"]["status"] == "uncertain"
        assert "you are still unsure" in student_mirror(view)
        assert {node["suggested_direction"] for node in view["influences"]["nodes"]} == {"Medicine"}


def test_social_media_and_pressure_do_not_override_counterfactual_own_choice():
    with StudentSession() as student, student.factory() as db:
        social = "TikTok says medicine is prestigious"
        count, _ = _student_turn(db, student, social, records=[
            _influence("TikTok", "Medicine", social, kind="social_media",
                       influence_type="social_message"),
            {"type": "goal", "data": {"goal_type": "degree_direction", "title": "Medicine"},
             "evidence": {"quote": social}, "attribution": {"claim_owner": "external"}},
        ])
        assert count == 1
        pressure = "I only want medicine because my family wants it"
        count, _ = _student_turn(db, student, pressure, records=[
            {"type": "goal", "data": {"goal_type": "degree_direction", "title": "Medicine"},
             "evidence": {"quote": pressure}, "attribution": {"claim_owner": "uncertain"}},
        ])
        assert count == 0
        own = "I would still choose design even if my parents disagreed"
        count, _ = _student_turn(db, student, own, records=[
            {"type": "student_voice_statement", "data": {"voice_type": "counterfactual",
                "statement": own, "direction": "Design", "commitment": "committed"},
             "evidence": {"quote": own}},
            {"type": "goal", "data": {"goal_type": "degree_direction", "title": "Design",
                "commitment": "committed"}, "evidence": {"quote": own}},
        ])
        assert count == 2
        view = StudentUnderstandingBuilder(db).build(student.workspace_id)
        assert [node["title"] for node in view["goals"]["nodes"]] == ["Design"]
        assert view["influences"]["nodes"][0]["source_type"] == "social_media"


def test_decision_readiness_is_specific_and_mirror_confirmation_is_not_a_verdict():
    records = {
        "education": [{"id": "school", "qualification_name": "A Levels",
                       "canonical_level": "upper_secondary", "result": {"grade": "Math A, Economics A, CS B"}}],
        "student_voice_statement": [{"id": "voice", "voice_type": "uncertainty",
                                     "statement": "I don't know what I want"}],
        "external_influence": [{"id": "father", "influencer_type": "parent",
                                "source_label": "father", "suggested_direction": "Computer Science",
                                "influence_type": "career_suggestion"}],
        "activity": [{"id": "business", "title": "Entrepreneurship competition",
                      "activity_type": "competition"}],
        "exploration_experience": [{"id": "cs", "domain": "computer_science",
                                    "activity_type": "field_exposure", "title": "Coding",
                                    "activity_status": "planned", "exposure_level": "none"}],
    }
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot(records),
        baseline={"status": "confirmed", "version": 1})
    intent = validated_decision_intent({"type": "choose_bachelor_direction",
                                        "candidates": ["Computer Science", "Economics"]})
    assert intent["type"] == "choose_bachelor_direction"
    result = DecisionSufficiencyEvaluator().evaluate(view, "choose_bachelor_direction",
        decision_intent=intent)
    assert result.baseline_confirmed and result.comparison_ready
    assert not result.recommendation_ready and result.next_best_move == "EXPLORE"
    assert "practical exposure to Computer Science" in result.missing_evidence
    state = CounselingEvaluator().derive(message="What should I choose, CS or Economics?",
        vault_context=view, journey=None, completion={"personalizedCounselingEligible": True},
        turn_semantics={"decision_intent": intent})
    policy = CounselingPolicy().decide(state)
    assert policy.personalized_advice_allowed and not policy.operator_allowed
    assert policy.move == "EXPLORE"
    assert policy.decision_sufficiency["comparison_ready"]
    assert "cannot recommend a final choice" in guard_premature_verdict(
        "Choose Economics. It is best for you.", result.to_dict())


def test_exploration_can_change_readiness_and_abroad_uses_other_evidence():
    records = {
        "education": [{"id": "school", "qualification_name": "A Levels",
                       "canonical_level": "upper_secondary", "result": {"grade": "A"}}],
        "goal": [{"id": "g", "goal_type": "degree_direction", "title": "Economics",
                  "commitment": "committed", "details": {"field_of_study": "Economics"}}],
        "exploration_experience": [
            {"id": "cs", "domain": "computer_science", "activity_type": "mini_project",
             "title": "Coding", "activity_status": "completed",
             "student_reflection": {"enjoyed": False}},
            {"id": "econ", "domain": "economics", "activity_type": "data_exercise",
             "title": "Data", "activity_status": "completed",
             "student_reflection": {"enjoyed": True}},
        ],
    }
    view = StudentUnderstandingBuilder().build("student", snapshot=_snapshot(records),
        baseline={"status": "confirmed", "version": 1})
    evaluator = DecisionSufficiencyEvaluator()
    intent = validated_decision_intent({"type": "choose_bachelor_direction",
                                        "candidates": ["Computer Science", "Economics"]})
    bachelor = evaluator.evaluate(view, "choose_bachelor_direction",
        decision_intent=intent)
    assert bachelor.recommendation_ready
    abroad = evaluator.evaluate(view, "study_abroad_direction",
        message="Which country should I choose to study abroad?")
    assert not abroad.recommendation_ready
    assert "funding context" in abroad.missing_evidence
    assert "practical exposure to Computer Science" not in abroad.missing_evidence
    assert evaluator.evaluate(view, "choose_bachelor_direction",
        decision_intent=intent) == bachelor


def test_golden_undecided_student_stays_in_exploration_after_confirmed_mirror():
    with StudentSession() as student, student.factory() as db:
        turns = [
            ("I don't know what I want to study", [{"type": "student_voice_statement",
                "data": {"voice_type": "uncertainty", "statement": "I don't know what I want to study"},
                "evidence": {"quote": "I don't know what I want to study"}}]),
            ("My father says CS", [_influence("father", "Computer Science", "My father says CS")]),
            ("My friend is doing BBA", [_influence("friend", "BBA", "My friend is doing BBA",
                kind="friend", influence_type="peer_path")]),
            ("I liked my entrepreneurship competition", [
                {"type": "activity", "data": {"activity_type": "competition",
                    "title": "entrepreneurship competition"},
                 "evidence": {"quote": "I liked my entrepreneurship competition"}},
                {"type": "student_voice_statement", "data": {"voice_type": "interest",
                    "statement": "I liked my entrepreneurship competition", "direction": "entrepreneurship"},
                 "evidence": {"quote": "I liked my entrepreneurship competition"}},
            ]),
            ("AI sounds cool", [{"type": "student_voice_statement", "data": {
                "voice_type": "interest", "statement": "AI sounds cool", "direction": "AI"},
                "evidence": {"quote": "AI sounds cool"}}]),
            ("I have never built code", [{"type": "exploration_experience", "data": {
                "domain": "computer_science", "activity_type": "field_exposure",
                "title": "coding", "exposure_level": "none"},
                "evidence": {"quote": "I have never built code"}}]),
        ]
        for message, records in turns:
            count, outcomes = _student_turn(db, student, message, records=records)
            assert count == len(records), message
            assert all(outcome.accepted for outcome in outcomes), message

        builder = StudentUnderstandingBuilder(db)
        view = builder.build(student.workspace_id)
        workspace = db.get(Workspace, student.workspace_id)
        save(workspace, status="mirror_review", view=view)
        mirror = student_mirror(view)
        assert "Outside influences" in mirror
        assert "father" in mirror and "friend" in mirror
        baseline = save(workspace, status="confirmed", view=view)
        db.commit()
        confirmed_view = builder.build(student.workspace_id, baseline=baseline)
        assert confirmed_view["student_voice"]["current_direction"]["status"] == "uncertain"
        state = CounselingEvaluator().derive(message="So what should I choose?",
            vault_context=confirmed_view, journey=None,
            completion={"personalizedCounselingEligible": True},
            turn_semantics={"decision_intent": {"type": "choose_bachelor_direction",
                                                 "candidates": []}})
        policy = CounselingPolicy().decide(state)
        assert policy.move == "EXPLORE"
        assert policy.personalized_advice_allowed
        assert not policy.decision_sufficiency["recommendation_ready"]
        assert "practical exposure to Computer Science" in policy.decision_sufficiency["missing_evidence"]
        answer = guard_premature_verdict("Choose CS. It is best for you.", policy.decision_sufficiency)
        assert "Choose CS" not in answer
