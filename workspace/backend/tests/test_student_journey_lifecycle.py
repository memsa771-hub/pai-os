"""Student Journey lifecycle, validation, and integration contracts."""

from unittest.mock import patch

import pytest

import app.capabilities as capability_module
from app.capabilities import CapabilityContract, CapabilityRegistry
from app.config import config
from app.journey import JourneyCoordinator, JourneyError, JourneyService
from app.counseling import runtime
from app.tools import ToolContext
from app.tools.builtin import capabilities as capability_tools
from scripts.counselor_eval_support import StudentSession


def test_conversation_creates_refines_and_deduplicates_a_journey():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        coordinator = JourneyCoordinator(service)
        first = coordinator.observe_message(student.workspace_id, "I want to do my master's in Germany.")
        again = coordinator.observe_message(student.workspace_id, "I want to do my master's in Germany.")
        refined = coordinator.observe_message(student.workspace_id, "I want MSc AI.")
        confirmed = coordinator.observe_message(student.workspace_id, "I confirm Germany.")
        db.commit()

        assert first.id == again.id == refined.id == confirmed.id
        assert len(service.list_active(student.workspace_id)) == 1
        assert refined.journey_type == "postgraduate_admission"
        assert refined.current_stage == "UNDERSTANDING"
        assert "MSc AI" in refined.target_outcome
        assert "Germany" in refined.target_outcome
        assert confirmed.decisions[0]["status"] == "confirmed"


def test_subgoals_independent_journeys_focus_and_dependencies_are_preserved():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        coordinator = JourneyCoordinator(service)
        admission = coordinator.observe_message(student.workspace_id, "I want a master's in Germany.")
        language = coordinator.observe_message(student.workspace_id, "I want to improve my IELTS.")
        internship = coordinator.observe_message(
            student.workspace_id, "I also want to get an internship while preparing."
        )
        db.commit()

        assert language.id == admission.id
        assert internship.id != admission.id
        assert len(service.list_active(student.workspace_id)) == 2
        assert sum(item.is_primary for item in service.list_active(student.workspace_id)) == 1
        language_goal = next(goal for goal in language.goals if goal["type"] == "subgoal")
        primary = next(goal for goal in language.goals if goal["type"] == "primary")
        assert language_goal["parent_goal_id"] == primary["id"]

        dependent = {
            "id": "documents", "parent_goal_id": primary["id"], "type": "subgoal",
            "title": "Prepare application documents", "status": "active",
            "priority": "medium", "depends_on": [language_goal["id"]],
        }
        updated = service.add_goal(student.workspace_id, admission.id, dependent)
        focused = service.set_focus_goal(student.workspace_id, admission.id, "documents")
        assert next(goal for goal in updated.goals if goal["id"] == "documents")["depends_on"] == [language_goal["id"]]
        assert focused.current_focus_goal_id == "documents"
        assert len(focused.goals) == 3


def test_stages_structured_state_and_events_are_validated():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        journey = JourneyCoordinator(service).observe_message(student.workspace_id, "I want to do a master's.")
        goal_id = journey.current_focus_goal_id
        assert service.set_stage(student.workspace_id, journey.id, "PLANNING").current_stage == "PLANNING"
        with pytest.raises(JourneyError, match="invalid journey stage"):
            service.set_stage(student.workspace_id, journey.id, "GUESSING")
        with pytest.raises(JourneyError, match="milestone.title"):
            service.upsert_milestone(student.workspace_id, journey.id, {
                "id": "profile", "goal_id": goal_id, "status": "pending",
            })

        service.upsert_milestone(student.workspace_id, journey.id, {
            "id": "profile", "goal_id": goal_id, "title": "Understand academic profile",
            "status": "completed", "completed_at": "2026-01-01T00:00:00Z",
        })
        service.add_decision(student.workspace_id, journey.id, {
            "id": "country", "goal_id": goal_id, "type": "target_country",
            "value": "Germany", "status": "confirmed", "source": "student",
        })
        service.add_blocker(student.workspace_id, journey.id, {
            "id": "transcript", "goal_id": goal_id, "type": "document",
            "description": "Transcript is not available", "status": "open",
        })
        resolved = service.resolve_blocker(student.workspace_id, journey.id, "transcript")
        assert resolved.blockers[0]["status"] == "resolved"
        assert service.get(student.workspace_id, journey.id).decisions[0]["status"] == "confirmed"
        event_types = {event["event_type"] for event in service.history(student.workspace_id, journey.id)}
        assert {"journey.stage_changed", "journey.milestone_updated", "journey.decision_added",
                "journey.blocker_added", "journey.blocker_resolved"}.issubset(event_types)


def test_completion_promotes_another_active_journey_and_keeps_vault_facts_out():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        coordinator = JourneyCoordinator(service)
        admission = coordinator.observe_message(student.workspace_id, "I want a master's in Germany.")
        internship = coordinator.observe_message(student.workspace_id, "I also want an internship.")
        completed = service.set_status(student.workspace_id, admission.id, "completed")
        db.commit()

        assert completed.status == "completed" and completed.current_stage == "COMPLETED"
        assert service.get_primary(student.workspace_id).id == internship.id
        assert "cgpa" not in completed.to_dict() and "nationality" not in completed.to_dict()
        assert service.history(student.workspace_id, admission.id)[-1]["event_type"] == "journey.completed"


def test_casual_questions_and_regional_qualification_assumptions_create_no_state():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        coordinator = JourneyCoordinator(service)
        assert coordinator.observe_message(
            student.workspace_id, "What is the difference between Matric and A Levels?"
        ) is None
        assert coordinator.observe_message(
            student.workspace_id, "Maybe I want to study abroad someday."
        ) is None
        assert service.list(student.workspace_id) == []


@pytest.mark.asyncio
async def test_capability_receives_only_declared_journey_fields():
    seen = {}

    async def handler(context, payload):
        seen.update(context.journey_context)
        return {"ok": True}

    with StudentSession() as student, student.factory() as db:
        JourneyCoordinator(JourneyService(db)).observe_message(student.workspace_id, "I want a master's in Germany.")
        db.commit()
        registry = CapabilityRegistry()
        registry.register(CapabilityContract(
            id="test.journey_scope", version="1.0.0", name="Scope", description="Scope test",
            input_schema={"type": "object"}, output_schema={"type": "object"}, handler=handler,
            journey_fields=frozenset({"current_objective", "target_outcome"}),
        ))
        ctx = ToolContext(student.workspace_id, "pai-operator", object())
        with patch.object(capability_module, "_registry", registry):
            result = await capability_tools.invoke(ctx, {"capability_id": "test.journey_scope", "input": {}})
        assert result["ok"]
        assert set(seen) == {"current_objective", "target_outcome"}


@pytest.mark.asyncio
async def test_counselor_turn_updates_and_receives_all_active_journeys():
    prompts = []

    async def model(**kwargs):
        prompts.append(kwargs["system_prompt"])
        return {"role": "assistant", "content": "Let's work through it."}

    with StudentSession() as student:
        with patch.object(runtime, "chat_completion_tools", model), \
                patch.object(config, "PAI_API_KEY", "test"), \
                patch.object(config, "PAI_MEMORY_CONTEXT_ENABLED", False):
            await student.turn("I want a master's in Germany.")
            await student.turn("I also want an internship while preparing.")
        with student.factory() as db:
            journeys = JourneyService(db).list_active(student.workspace_id)
        assert len(journeys) == 2
        assert "postgraduate_admission" in prompts[-1]
        assert "internship_search" in prompts[-1]
        assert "current_focus_goal" in prompts[-1]
