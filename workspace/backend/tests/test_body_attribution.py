# -*- coding: utf-8 -*-
"""No request body decides who acted.

The event pipeline was fixed first (see test_event_identity.py), but five more
routers kept reading attribution straight out of the body: notifications,
timers, to-dos, workflows, and file deletion. Each is the same defect —
`created_by` / `source` taken from a field the caller controls — and each was
reachable by anyone holding the workspace machine token, which every agent in
a workspace shares.

`/v1/todos` was the sharpest: it replaces "the calling agent's" entire list,
scoped by `body.source`, so a caller could erase another agent's to-dos by
naming it.
"""

import pytest

from tests.conftest import make_owned_workspace


@pytest.fixture
def ws(client):
    return make_owned_workspace()


def _agent_headers(ws):
    """A real, credentialed agent: machine token PLUS its live session."""
    return {"X-Workspace-Token": ws["token"], "X-Session-Id": ws["sessionId"]}


def _token_only(ws):
    """The shared machine token and nothing else — proves access, names nobody."""
    return {"X-Workspace-Token": ws["token"]}


class TestAttributionIsDerived:
    def test_notification_source_comes_from_credentials(self, client, ws):
        resp = client.post("/v1/notifications", json={
            "network": ws["workspaceId"], "title": "T", "message": "M",
            "source": "openagents:pai",          # ignored
        }, headers=_agent_headers(ws))
        assert resp.status_code == 200
        assert resp.json()["data"]["created_by"] == f"openagents:{ws['agentName']}"

    def test_timer_created_by_comes_from_credentials(self, client, ws, db):
        from app.models import TimerRecord

        resp = client.post("/v1/timers", json={
            "network": ws["workspaceId"], "delay": 60, "message": "M",
            "source": "openagents:pai",          # ignored
        }, headers=_agent_headers(ws))
        assert resp.status_code == 200

        timer = db.query(TimerRecord).filter_by(
            workspace_id=ws["workspaceId"]).one()
        assert timer.created_by == f"openagents:{ws['agentName']}"

    def test_workflow_created_by_comes_from_credentials(self, client, ws):
        resp = client.post("/v1/workflows", json={
            "network": ws["workspaceId"], "name": "WF",
            "steps": [{"name": "s1", "instruction": "do s1",
                       "assignee": {"kind": "agent", "agent": ws["agentName"]}}],
            "source": "human:somebody-else",     # ignored
        }, headers=_agent_headers(ws))
        assert resp.status_code == 200
        assert resp.json()["data"]["created_by"] == f"openagents:{ws['agentName']}"

    def test_todos_are_scoped_to_the_caller_not_the_body(self, client, ws, db):
        from app.models import TodoRecord

        resp = client.put("/v1/todos", json={
            "network": ws["workspaceId"],
            "todos": [{"content": "mine", "status": "pending"}],
            "source": "openagents:some-other-agent",   # ignored
        }, headers=_agent_headers(ws))
        assert resp.status_code == 200

        rows = db.query(TodoRecord).filter_by(workspace_id=ws["workspaceId"]).all()
        assert [r.created_by for r in rows] == [f"openagents:{ws['agentName']}"]

    def test_file_delete_event_is_attributed_to_the_deleter(self, client, ws, db):
        from app.models import EventRecord

        upload = client.post(
            "/v1/files",
            data={"network": ws["workspaceId"]},
            files={"file": ("a.txt", b"x", "text/plain")},
            headers=_agent_headers(ws),
        )
        file_id = upload.json()["data"]["id"]

        assert client.delete(f"/v1/files/{file_id}",
                             headers=_agent_headers(ws)).status_code == 200

        event = db.query(EventRecord).filter_by(
            type="workspace.file.deleted").one()
        # Was hardcoded "human:user" — a deletion by an agent claimed to be one
        # by the student.
        assert event.source == f"openagents:{ws['agentName']}"


class TestUnidentifiedCallersRejected:
    """The shared machine token authorizes, but it does not identify."""

    @pytest.mark.parametrize("path,body", [
        ("/v1/notifications", {"title": "T", "message": "M"}),
        ("/v1/timers", {"delay": 60, "message": "M"}),
        ("/v1/workflows", {"name": "WF", "steps": []}),
    ])
    def test_token_without_a_session_cannot_write(self, client, ws, path, body):
        resp = client.post(path, json={"network": ws["workspaceId"], **body},
                           headers=_token_only(ws))
        assert resp.status_code == 401

    def test_todos_put_without_a_session_cannot_write(self, client, ws):
        resp = client.put("/v1/todos",
                          json={"network": ws["workspaceId"], "todos": []},
                          headers=_token_only(ws))
        assert resp.status_code == 401


class TestSourceIsNotEvenAccepted:
    def test_source_is_gone_from_the_request_schemas(self, client):
        """A field the server ignores should not be advertised as input."""
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        for name in ("CreateNotificationRequest", "CreateTimerRequest",
                     "CreateWorkflowRequest", "PutTodosRequest"):
            assert "source" not in schemas[name]["properties"], name
