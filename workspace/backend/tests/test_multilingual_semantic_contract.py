"""The same canonical claims pass independently of the student's language."""

from app.counseling.decision_sufficiency import (
    DecisionSufficiencyEvaluator, validated_decision_intent)
from app.counseling.turn_semantics import validate_turn_semantics
from app.counseling.understanding import StudentUnderstandingBuilder
from app.memory.extractor import _validate
from app.memory.student_snapshot import StudentSnapshot
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import asyncio
import json

from app.counseling.turn_semantics import classify_turn


def _turn(message):
    return SimpleNamespace(user_text=message, user_event_id="owner-event", records={})


def test_mixed_ownership_has_equivalent_structure_in_four_languages():
    examples = (
        ("My father wants medicine, but I prefer design.", "I prefer design", "father"),
        ("میرے والد طب چاہتے ہیں لیکن میں ڈیزائن پسند کرتا ہوں", "میں ڈیزائن پسند کرتا ہوں", "والد"),
        ("والدي يريد الطب لكنني أفضل التصميم", "أفضل التصميم", "والدي"),
        ("Mi padre quiere medicina, pero prefiero diseño", "prefiero diseño", "padre"),
    )
    for message, own_clause, source_label in examples:
        external = _validate({"candidate_type": "student_record", "key": "external_influence",
            "proposed_value": {"influencer_type": "parent", "source_label": source_label,
                "suggested_direction": "Medicine", "influence_type": "career_suggestion"},
            "quote": message, "attribution": {"claim_owner": "mixed"}}, _turn(message), set())
        voice = _validate({"candidate_type": "student_record", "key": "student_voice_statement",
            "proposed_value": {"voice_type": "preference", "statement": own_clause,
                               "direction": "Design", "commitment": "considering"},
            "quote": message, "attribution": {"claim_owner": "mixed",
                                              "student_clause_quote": own_clause}}, _turn(message), set())
        goal = _validate({"candidate_type": "student_record", "key": "goal",
            "proposed_value": {"goal_type": "degree_direction", "title": "Design"},
            "quote": message, "attribution": {"claim_owner": "mixed",
                                              "student_clause_quote": own_clause}}, _turn(message), set())
        rejected = _validate({"candidate_type": "student_record", "key": "goal",
            "proposed_value": {"goal_type": "degree_direction", "title": "Medicine"},
            "quote": message, "attribution": {"claim_owner": "external"}}, _turn(message), set())
        assert external and voice and goal and rejected is None
        assert external.evidence["quote"] == message
        assert voice.proposed_value["direction"] == "Design"


def test_decision_intent_accepts_unbounded_fields_and_unicode_keys():
    snapshot = StudentSnapshot("student", {}, {
        "education": [{"id": "school", "qualification_name": "School",
                       "canonical_level": "upper_secondary", "result": {"grade": "A"}}],
        "student_voice_statement": [{"id": "v", "voice_type": "uncertainty",
                                     "statement": "undecided"}],
        "external_influence": [{"id": "outside", "influencer_type": "friend",
                                "source_label": "friend", "suggested_direction": "Accounting",
                                "influence_type": "peer_path"}],
    }, (), datetime.now(timezone.utc))
    view = StudentUnderstandingBuilder().build("student", snapshot=snapshot,
        baseline={"status": "confirmed", "version": 1})
    for names in (["Law", "Psychology"], ["Architecture", "Civil Engineering"],
                  ["Neuroethology", "海洋生物学"]):
        intent = validated_decision_intent({"type": "choose_bachelor_direction",
                                            "candidates": names})
        result = DecisionSufficiencyEvaluator().evaluate(view, intent["type"],
                                                          decision_intent=intent)
        assert [item["name"] for item in result.candidate_directions] == names
        assert all(item["key"] for item in result.candidate_directions)
        assert result.next_best_move == "EXPLORE"


def test_multilingual_classifier_output_validates_identically():
    messages = ("I am unsure", "مجھے یقین نہیں", "لست متأكدا", "No estoy seguro")
    for message in messages:
        semantic = validate_turn_semantics({
            "decision_intent": {"type": "choose_bachelor_direction",
                                "candidates": ["Law", "Psychology"]},
            "discovery_statuses": [{"focus": "current_direction", "status": "UNKNOWN",
                                    "quote": message}],
        }, message=message)
        assert semantic["decision_intent"]["candidates"] == ("Law", "Psychology")
        assert semantic["discovery_statuses"][0]["status"] == "UNKNOWN"


def test_classifier_preserves_multilingual_message_as_model_input():
    message = "میرے والد طب چاہتے ہیں لیکن میں ڈیزائن پسند کرتا ہوں"
    response = json.dumps({"decision_intent": {"type": "compare_fields",
                                                "candidates": ["Medicine", "Design"]}},
                          ensure_ascii=False)
    with patch("app.counseling.turn_semantics.chat_completion",
               new_callable=AsyncMock) as model:
        model.return_value = response
        result = asyncio.run(classify_turn(message))
    assert result["decision_intent"]["candidates"] == ("Medicine", "Design")
    assert message in model.await_args.kwargs["messages"][0]["content"]
