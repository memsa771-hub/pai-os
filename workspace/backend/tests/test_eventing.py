"""Behavior contracts for the PAI OS event pipeline."""

import pytest

from app.eventing.events import Event, PAI_MESSAGE_POSTED_EVENT_TYPE
from app.eventing.mods import EventRejected, GuardMod, ObserveMod, PipelineContext, TransformMod
from app.eventing.pipeline import Pipeline
from app.mods.workspace_mod import WorkspaceMod, _HANDLERS


class _Guard(GuardMod):
    name = "test-guard"
    priority = 10

    def __init__(self, calls, *, reject=False):
        self.calls = calls
        self.reject = reject

    async def process(self, event, context):
        self.calls.append("guard")
        return None if self.reject else event


class _Transform(TransformMod):
    name = "test-transform"
    priority = 10

    def __init__(self, calls):
        self.calls = calls

    async def process(self, event, context):
        self.calls.append("transform")
        event.metadata["transformed"] = True
        return event


class _Observe(ObserveMod):
    name = "test-observe"
    priority = 10

    def __init__(self, calls):
        self.calls = calls

    async def process(self, event, context):
        self.calls.append("observe")
        return None


@pytest.mark.asyncio
async def test_pipeline_preserves_guard_transform_observe_order():
    calls = []
    pipeline = Pipeline([_Observe(calls), _Transform(calls), _Guard(calls)])
    event = Event(
        type="workspace.message.posted",
        source="openagents:pai",
        target="channel/student",
        payload={"content": "hello"},
    )

    result = await pipeline.process(event, PipelineContext("workspace"))

    assert calls == ["guard", "transform", "observe"]
    assert result is event
    assert result.metadata == {"transformed": True}
    assert result.type == "workspace.message.posted"
    assert result.source == "openagents:pai"


@pytest.mark.asyncio
async def test_guard_rejection_stops_the_pipeline():
    calls = []
    pipeline = Pipeline([_Observe(calls), _Transform(calls), _Guard(calls, reject=True)])
    event = Event(type="network.agent.join", source="openagents:worker", target="core")

    with pytest.raises(EventRejected, match="test-guard"):
        await pipeline.process(event, PipelineContext("workspace"))

    assert calls == ["guard"]


def test_pai_message_event_type_keeps_existing_wire_value():
    assert PAI_MESSAGE_POSTED_EVENT_TYPE == "workspace.message.posted"


def test_dead_compatibility_api_is_not_exposed():
    context = PipelineContext("workspace")
    pipeline = Pipeline([])

    assert not hasattr(Event, "as_reply")
    assert not hasattr(context, "side_effects")
    assert not hasattr(context, "emit")
    assert not hasattr(pipeline, "add_mod")
    assert not hasattr(pipeline, "remove_mod")


def test_workspace_handlers_are_split_by_domain():
    expected_modules = {
        "network.agent.join": "app.eventing.handlers.agents",
        "network.agent.leave": "app.eventing.handlers.agents",
        "network.agent.remove": "app.eventing.handlers.agents",
        "network.ping": "app.eventing.handlers.agents",
        "network.channel.create": "app.eventing.handlers.channels",
        "network.channel.join": "app.eventing.handlers.channels",
        "network.channel.leave": "app.eventing.handlers.channels",
        PAI_MESSAGE_POSTED_EVENT_TYPE: "app.eventing.handlers.messages",
    }

    assert set(_HANDLERS) == set(expected_modules)
    for event_type, module_name in expected_modules.items():
        assert _HANDLERS[event_type].__module__ == module_name


@pytest.mark.asyncio
async def test_workspace_mod_dispatches_and_passes_through(monkeypatch):
    async def handler(event, context):
        event.metadata["handled"] = context.network_id
        return event

    monkeypatch.setitem(_HANDLERS, "test.workspace.event", handler)
    mod = WorkspaceMod()
    context = PipelineContext("workspace-1")
    handled = Event(type="test.workspace.event", source="human:test", target="core")
    untouched = Event(type="test.unknown", source="human:test", target="core")

    assert await mod.process(handled, context) is handled
    assert handled.metadata == {"handled": "workspace-1"}
    assert await mod.process(untouched, context) is untouched
