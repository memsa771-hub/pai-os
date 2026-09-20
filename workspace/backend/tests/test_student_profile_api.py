"""The Profile HTTP contract, exercised through the real ASGI app.

`test_student_records.py` covers the services this composes. These tests cover
the wire: that the endpoints are mounted, that the projection serializes, that
a restricted identifier does not survive the trip to the browser, and that an
edit posted by the Profile page is the same canonical write PAI's extraction
makes — visible to the Counselor on its next turn.

The engine differs from the shared one on purpose: TestClient serves the app
on another thread, so the SQLite connection has to be shareable across threads.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.memory.field_definitions import SEED_FIELD_DEFINITIONS, VaultFieldDefinitionService
from app.memory.student_context import StudentContextBuilder
from app.memory.student_records import ENTITY_MODELS
from app.memory.vault import VaultService
from app.models import (
    BackgroundJob, EventRecord, FileRecord, MemoryCandidate, PaiEpisode, PaiMemory,
    ProfileIssue, StudentRecordRevision, User, VaultFact, VaultFieldDefinition, Workspace,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


TOKEN = "machine-secret"
HEADERS = {"X-Workspace-Token": TOKEN}

# One of each sensitivity class, mirroring migration 063.
EXTRA_FIELDS = (
    ("identity.current_status", "normal"),
    ("location.current_city", "normal"),
    ("location.current_country", "normal"),
    ("identity.passport_number", "restricted"),
)


@pytest.fixture
def api():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _setup(connection, record):
        connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())
        connection.execute("PRAGMA foreign_keys=ON")

    tables = [User, Workspace, EventRecord, FileRecord, VaultFact, VaultFieldDefinition,
              MemoryCandidate, PaiMemory, PaiEpisode, ProfileIssue, StudentRecordRevision,
              BackgroundJob, *ENTITY_MODELS.values()]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in tables])

    session = Session(engine, autoflush=False)
    user = User(email="student@example.test", display_name="Ali Ahmed")
    session.add(user)
    session.flush()
    workspace = Workspace(name="Student", owner_user_id=user.id, password_hash=TOKEN)
    session.add(workspace)
    session.flush()
    fields = VaultFieldDefinitionService(session)
    for spec in SEED_FIELD_DEFINITIONS:
        fields.upsert_definition(spec)
    for key, sensitivity in EXTRA_FIELDS:
        fields.upsert_definition({"key": key, "category": key.split(".")[0],
                                  "sensitivity": sensitivity, "data_type": "string",
                                  "validation_schema": {"type": "string"}})
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app), session, str(workspace.id)
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        engine.dispose()


def _get(client, network, path=""):
    response = client.get(f"/v1/student-profile{path}?network={network}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _edit(client, network, **payload):
    response = client.post(f"/v1/student-profile/edits?network={network}",
                           headers=HEADERS, json={"reason": "Corrected on profile", **payload})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_empty_profile_still_serves_account_identity(api):
    client, _, network = api
    data = _get(client, network)
    assert data["meta"]["isEmpty"] is True
    assert data["header"] == {"displayName": "Ali Ahmed", "email": "student@example.test"}
    # The shape is stable even when there is nothing in it, so the page never
    # has to guard every section against a missing key.
    assert set(data) == {"header", "facts", "factGroups", "sections", "readiness", "issues", "meta"}
    assert data["sections"]["education"]["education"] == []


def test_multiple_education_records_round_trip_over_http(api):
    client, _, network = api
    _edit(client, network, record_type="education", value={
        "qualification_name": "BS Computer Science", "institution_name": "COMSATS",
        "start_date": "2022", "end_date": "2026", "academic_status": "current",
        "result": {"gpa": 3.42, "gpa_scale": 4}})
    _edit(client, network, record_type="education", value={
        "qualification_name": "FSc Pre-Engineering", "institution_name": "Punjab College",
        "start_date": "2020", "end_date": "2022", "result": {"percentage": 87}})

    education = _get(client, network)["sections"]["education"]["education"]
    assert len(education) == 2
    rows = {row["qualification_name"]: row for row in education}
    assert rows["BS Computer Science"]["result"] == {"gpa": 3.42, "gpa_scale": 4}
    assert rows["BS Computer Science"]["institution_name"] == "COMSATS"
    assert rows["FSc Pre-Engineering"]["result"] == {"percentage": 87}
    # Each record keeps its own grading system: a percentage is not coerced
    # into a GPA, and neither one collapses the other.
    assert "gpa" not in rows["FSc Pre-Engineering"]["result"]


def test_header_derives_status_from_a_posted_vault_fact(api):
    client, _, network = api
    _edit(client, network, field_key="identity.current_status",
          value="Final-year BS Computer Science Student")
    _edit(client, network, field_key="location.current_city", value="Islamabad")
    _edit(client, network, field_key="location.current_country", value="Pakistan")

    header = _get(client, network)["header"]
    assert header["status"] == "Final-year BS Computer Science Student"
    assert header["location"] == "Islamabad, Pakistan"
    assert header["displayName"] == "Ali Ahmed"


def test_restricted_identifier_never_reaches_the_profile_response(api):
    client, session, network = api
    VaultService(session).apply_fact(network, "identity.passport_number",
                                     "SECRET-PASSPORT", "user_explicit")
    session.commit()

    profile = client.get(f"/v1/student-profile?network={network}", headers=HEADERS)
    assert "SECRET-PASSPORT" not in profile.text

    # The operator/export view is a separate endpoint and still has it, which
    # is what makes the Profile's omission a deliberate projection rather than
    # the value having simply not been stored.
    raw = client.get(f"/v1/student-profile/raw?network={network}", headers=HEADERS)
    assert "SECRET-PASSPORT" in raw.text


def test_profile_edit_updates_the_same_record_and_keeps_its_history(api):
    client, _, network = api
    created = _edit(client, network, record_type="education", value={
        "qualification_name": "BS CS", "institution_name": "COMSATS",
        "result": {"gpa": 3.42, "gpa_scale": 4}})
    record_id = created["id"]

    corrected = _edit(client, network, record_type="education", record_id=record_id,
                      value={"result": {"gpa": 3.52, "gpa_scale": 4}})
    assert corrected["id"] == record_id, "a correction must not fork a second record"

    education = _get(client, network)["sections"]["education"]["education"]
    assert len(education) == 1
    assert education[0]["result"] == {"gpa": 3.52, "gpa_scale": 4}
    # Merged, not replaced: the untouched fields survive a partial edit.
    assert education[0]["institution_name"] == "COMSATS"

    history = client.get(
        f"/v1/student-profile/history?network={network}"
        f"&record_type=education&record_id={record_id}", headers=HEADERS)
    assert history.status_code == 200, history.text
    trail = history.json()["data"]["revisions"]
    assert [r["captureMethod"] for r in trail] == ["explicit_correction", "explicit_correction"]
    assert trail[0]["after"]["result"]["gpa"] == 3.42
    assert trail[-1]["before"]["result"]["gpa"] == 3.42


def test_profile_edit_is_visible_to_the_counselor(api):
    """The loop: Profile correction -> canonical model -> Counselor context."""
    client, session, network = api
    created = _edit(client, network, record_type="education", value={
        "qualification_name": "BS CS", "institution_name": "COMSATS",
        "result": {"gpa": 3.42, "gpa_scale": 4}})
    _edit(client, network, record_type="education", record_id=created["id"],
          value={"result": {"gpa": 3.52, "gpa_scale": 4}})

    context = StudentContextBuilder(session).build_context(
        network, query="What should I study?")
    assert context.records["education"][0]["result"]["gpa"] == 3.52


def test_edit_endpoint_rejects_an_unknown_record_type_and_ambiguous_payloads(api):
    client, _, network = api
    both = client.post(f"/v1/student-profile/edits?network={network}", headers=HEADERS, json={
        "record_type": "education", "field_key": "identity.current_status",
        "value": "x", "reason": "r"})
    assert both.json()["code"] != 0

    unknown = client.post(f"/v1/student-profile/edits?network={network}", headers=HEADERS, json={
        "record_type": "not_a_record", "value": {"x": 1}, "reason": "r"})
    assert unknown.json()["code"] != 0


def test_every_profile_endpoint_requires_credentials(api):
    client, _, network = api
    assert client.get(f"/v1/student-profile?network={network}").status_code == 401
    assert client.get(f"/v1/student-profile/raw?network={network}").status_code == 401
    assert client.post(f"/v1/student-profile/edits?network={network}",
                       json={"record_type": "skill", "value": {"name": "Python"},
                             "reason": "r"}).status_code == 401


def test_open_issues_surface_without_their_restricted_evidence(api):
    client, session, network = api
    vault = VaultService(session)
    # Two conversational claims about a restricted field raise a blocking issue
    # whose stored evidence carries the proposed value inline.
    vault.apply_fact(network, "identity.passport_number", "FIRST-PASSPORT", "conversation")
    vault.apply_fact(network, "identity.passport_number", "SECOND-PASSPORT", "conversation")
    session.commit()

    response = client.get(f"/v1/student-profile?network={network}", headers=HEADERS)
    data = response.json()["data"]
    assert data["issues"], "the student must still learn something needs review"
    assert "SECOND-PASSPORT" not in response.text
    assert all(issue["values"] is None for issue in data["issues"])
    assert data["meta"]["openIssueCount"] == len(data["issues"])


def test_resolving_an_issue_clears_it_from_the_projection(api):
    client, session, network = api
    vault = VaultService(session)
    vault.apply_fact(network, "identity.current_status", "Student", "conversation")
    vault.apply_fact(network, "identity.current_status", "Graduate", "conversation")
    session.commit()

    issues = _get(client, network)["issues"]
    assert issues
    resolve = client.post(
        f"/v1/student-profile/issues/{issues[0]['id']}/resolve?network={network}",
        headers=HEADERS, json={"note": "This is settled"})
    assert resolve.status_code == 200 and resolve.json()["data"]["resolved"] is True
    assert not _get(client, network)["issues"]


def test_projection_carries_every_section_and_stage(api):
    client, _, network = api
    data = _get(client, network)
    assert set(data["sections"]) == {
        "education", "tests", "experience", "projects", "skills", "certifications",
        "research", "goals", "finance", "applications", "documents"}
    assert set(data["factGroups"]) == {"about", "preferences", "finance"}
    assert data["readiness"]["primary"]["stage"] == "discovery"
    assert len(data["readiness"]["stages"]) == 9
    # Nothing in the payload is a nested raw model dump the page would have to
    # stringify — every section is a dict of record-kind lists.
    assert all(isinstance(kinds, dict) and all(isinstance(v, list) for v in kinds.values())
               for kinds in data["sections"].values())
    json.dumps(data)  # the whole projection must be JSON-serializable
