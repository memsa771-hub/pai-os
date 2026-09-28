"""Core architecture contracts. No domain plugin behavior belongs here."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.capabilities import CapabilityContract, CapabilityRegistry, CapabilityRouter
from app.capabilities.context import CapabilityExecutionContext
from app.capabilities.manifest import InvalidCapabilityManifest
from app.capabilities.permissions import CapabilityAccessDenied
from app.capabilities.router import CapabilityNotFound
from app.counseling import CounselingEvaluator, CounselingMove, CounselingPolicy
from app.journey import JourneyService
from app.memory.student_context_gateway import StudentContextAccessDenied, StudentContextGateway
from app.models import ExecutionRun
from app.services import operator
from app.tools import ToolContext
from scripts.counselor_eval_support import StudentSession


async def _echo(context, payload):
    return {"echo": payload["text"], "scopes": sorted(context.student_context)}


def _contract(**changes):
    values = dict(
        id="example.echo", version="1.0.0", name="Echo", description="Echo test input.",
        input_schema={"type": "object"}, output_schema={"type": "object"}, handler=_echo,
    )
    values.update(changes)
    return CapabilityContract(**values)


def test_journey_supports_lifetime_history_and_one_resolved_primary():
    with StudentSession() as student, student.factory() as db:
        service = JourneyService(db)
        first = service.create(student.workspace_id, "undergraduate_admission", "First degree", primary=True)
        service.set_status(student.workspace_id, first.id, "completed")
        second = service.create(
            student.workspace_id, "career_transition", "Move into product", primary=True,
            current_stage="planning", current_objective="Choose a bridge route",
            milestones=[{"id": "skills", "status": "pending"}],
        )
        db.commit()

        assert len(service.list(student.workspace_id)) == 2
        assert service.resolve_primary(student.workspace_id).id == second.id
        updated = service.update(student.workspace_id, second.id, blockers=[{"reason": "portfolio"}])
        assert updated.blockers == [{"reason": "portfolio"}]
        assert [e["event_type"] for e in service.history(student.workspace_id, second.id)] == [
            "journey.created", "journey.updated",
        ]


def test_counseling_policy_blocks_planning_and_conflict_forces_clarification():
    evaluator = CounselingEvaluator()
    incomplete = evaluator.derive(
        message="Make my roadmap", vault_context={}, journey={"current_objective": "shortlist"},
        completion={"personalizedCounselingEligible": False,
                    "missingCritical": [{"key": "education.history"}]},
    )
    decision = CounselingPolicy().decide(incomplete)
    assert not decision.personalized_advice_allowed
    assert not decision.roadmap_allowed
    assert not decision.operator_allowed

    conflict = evaluator.derive(
        message="I already completed a PhD", vault_context={},
        journey={"current_objective": "undergraduate BBA"},
        completion={"personalizedCounselingEligible": True},
        active_conflict={"summary": "degree level conflicts"},
    )
    assert conflict.next_move is CounselingMove.CLARIFY
    assert CounselingPolicy().decide(conflict).max_questions == 1


@pytest.mark.asyncio
async def test_capability_contract_registry_router_and_permissions_fail_closed():
    from app.memory.permissions import OPERATOR_CAPABILITIES
    from app.tools import AUDIENCE_OPERATOR, get_tool_registry

    tools = get_tool_registry()
    tool_names = {tool.name for tool in tools.for_audience(AUDIENCE_OPERATOR)
                  if tools.permits(tool, OPERATOR_CAPABILITIES)}
    assert "capability.invoke" in tool_names
    registry = CapabilityRegistry()
    registry.register(_contract(permissions=frozenset({"web.read"})))
    with pytest.raises(ValueError, match="Duplicate capability"):
        registry.register(_contract())
    with pytest.raises(InvalidCapabilityManifest):
        CapabilityRegistry().register(_contract(id="Not Valid"))

    router = CapabilityRouter(registry)
    with pytest.raises(CapabilityNotFound):
        router.resolve("missing.capability")
    context = CapabilityExecutionContext("w", {}, {}, frozenset(), frozenset())
    with pytest.raises(CapabilityAccessDenied):
        await router.execute("example.echo", {"text": "hello"}, context)
    allowed = CapabilityExecutionContext("w", {"identity": {}}, {}, frozenset({"web.read"}), frozenset())
    assert (await router.execute("example.echo", {"text": "hello"}, allowed))["echo"] == "hello"

    # A future registration is immediately discoverable; Operator has no id switch statement.
    registry.register(_contract(id="example.second", version="2.0.0"))
    assert router.resolve("example.second").version == "2.0.0"


def test_context_gateway_is_scoped_and_uses_existing_canonical_store():
    with StudentSession() as student, student.factory() as db:
        gateway = StudentContextGateway(db)
        with pytest.raises(StudentContextAccessDenied):
            gateway.get(student.workspace_id, ["education"], caller="untrusted-agent")
        scoped = gateway.get(student.workspace_id, ["education"], caller="pai")
        assert scoped.scopes == ("education",)
        assert set(scoped.domains) == {"education"}
        assert not hasattr(gateway, "write")  # facts still flow through proposal/reconciliation


@pytest.mark.asyncio
async def test_operator_resumes_same_run_and_preserves_completed_actions():
    with StudentSession() as student:
        with student.factory() as db:
            run = ExecutionRun(
                workspace_id=student.workspace_id, requested_by="openagents:pai",
                objective="Prepare a draft", status="needs_user_action",
                pending_action={"kind": "approval", "required": True},
                plan=[{"id": "read", "title": "Read source", "status": "completed"}],
                completed_steps=["Read source"],
                tool_calls=[{"tool": "files.read", "arguments": {"file_id": "f1"}, "ok": True}],
            )
            db.add(run)
            db.commit()
            run_id = run.id

        ctx = ToolContext(student.workspace_id, "pai", object())
        with patch.object(operator, "_execute", AsyncMock()) as execute:
            result = await operator.resume(ctx, run_id, {"approved": True})
            await asyncio.sleep(0)
        assert result["data"]["run_id"] == run_id
        assert execute.call_args.args[0] == run_id
        with student.factory() as db:
            resumed = db.get(ExecutionRun, run_id)
            assert resumed.completed_at is None
            assert resumed.completed_steps == ["Read source"]
            assert resumed.tool_calls[0]["arguments"] == {"file_id": "f1"}
