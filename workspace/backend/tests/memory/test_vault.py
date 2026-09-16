# -*- coding: utf-8 -*-
"""Vault: dynamic field definitions, validation, provenance, isolation.

The central claim under test is that the Vault has no hardcoded fields — a
field invented inside a test must work end to end without touching any code.
"""

import pytest
from sqlalchemy import select

from app.memory.field_definitions import VaultFieldDefinitionService, VaultFieldError
from app.memory.vault import VaultOutcome, VaultService
from app.models import VaultFact


# ---------------------------------------------------------------------------
# Dynamic field definitions
# ---------------------------------------------------------------------------

def test_vault_accepts_a_field_invented_at_runtime(db_session, workspace):
    """A field nobody wrote code for works immediately.

    This is the anti-hardcoding requirement: if any branch keyed on a field
    name existed, `career.skills` would have to be added to it first.
    """
    fields = VaultFieldDefinitionService(db_session)
    fields.upsert_definition({
        "key": "career.skills",
        "category": "career",
        "data_type": "array",
        "validation_schema": {"type": "array", "items": {"type": "string"}},
        "cardinality": "multi",
    })

    vault = VaultService(db_session)
    result = vault.apply_fact(
        workspace_id=workspace.id,
        field_key="career.skills",
        value=["python", "sql"],
        source_type="user_explicit",
    )

    assert result.outcome is VaultOutcome.CREATED
    assert result.fact.field_key == "career.skills"
    # A list-valued field reads back as the list itself, never wrapped again.
    assert vault.snapshot(workspace.id)["career.skills"] == ["python", "sql"]


def test_definition_upsert_versions_rather_than_mutates(db_session):
    """Editing a definition supersedes it, so old facts stay interpretable."""
    fields = VaultFieldDefinitionService(db_session)
    first = fields.upsert_definition({
        "key": "tests.pte.score", "category": "tests", "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 0, "maximum": 90},
    })
    assert first.version == 1

    second = fields.upsert_definition({
        "key": "tests.pte.score", "category": "tests", "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 10, "maximum": 90},
    })
    assert second.version == 2
    # `get` returns the live one; the superseded row is retained.
    assert fields.get("tests.pte.score").version == 2


def test_unknown_field_is_rejected(db_session, workspace):
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError, match="Unknown vault field"):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="not.a.real.field",
            value=1, source_type="user_explicit",
        )


# ---------------------------------------------------------------------------
# Validation — values must satisfy their definition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [11, -1, 10.5])
def test_out_of_range_numbers_are_rejected(db_session, workspace, seed_fields, value):
    """education.cgpa is bounded 0..10 by its definition, not by code."""
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="education.cgpa",
            value=value, source_type="user_explicit",
        )


def test_wrong_type_is_rejected(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError, match="must be number"):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="education.cgpa",
            value="eight point one", source_type="user_explicit",
        )


def test_enum_violation_is_rejected(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError, match="must be one of"):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="preferences.study_mode",
            value="telepathic", source_type="user_explicit",
        )


def test_object_missing_required_key_is_rejected(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError, match="Missing required argument"):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="finance.budget",
            value={"amount": 30000},  # no currency
            source_type="user_explicit",
        )


def test_rejected_value_is_not_persisted(db_session, workspace, seed_fields):
    """A rejected write must leave no trace — not even a superseded row."""
    vault = VaultService(db_session)
    with pytest.raises(VaultFieldError):
        vault.apply_fact(
            workspace_id=workspace.id, field_key="education.cgpa",
            value=99, source_type="user_explicit",
        )
    rows = db_session.execute(
        select(VaultFact).where(VaultFact.workspace_id == workspace.id)
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# Provenance and history
# ---------------------------------------------------------------------------

def test_superseding_keeps_history_with_provenance(db_session, workspace, seed_fields):
    """Updating a fact must not destroy the previous one."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=7.5,
        source_type="document", source_event_id="evt-1",
        evidence={"quote": "CGPA 7.5"},
    )
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit", source_event_id="evt-2",
    )

    history = vault.history(workspace.id, "education.cgpa")
    assert len(history) == 2

    active = [f for f in history if f.status == "active"]
    superseded = [f for f in history if f.status == "superseded"]
    assert len(active) == 1 and len(superseded) == 1
    assert active[0].value["value"] == 8.1
    assert active[0].source_type == "user_explicit"
    # The old value keeps its own provenance and gets an end date.
    assert superseded[0].value["value"] == 7.5
    assert superseded[0].source_event_id == "evt-1"
    assert superseded[0].evidence == {"quote": "CGPA 7.5"}
    assert superseded[0].valid_until is not None


def test_inference_cannot_overwrite_an_explicit_user_statement(db_session, workspace, seed_fields):
    """The student said 8.1; a chat inference of 6.0 must not silently win."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=6.0,
        source_type="conversation", confidence=0.9,
    )
    assert vault.get_fact(workspace.id, "education.cgpa").value["value"] == 8.1


def test_highest_confidence_policy_prefers_stronger_source(db_session, workspace, seed_fields):
    """tests.ielts.score declares highest_confidence, so trust ranking applies."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="tests.ielts.score", value=6.5,
        source_type="conversation", confidence=0.9,
    )
    vault.apply_fact(
        workspace_id=workspace.id, field_key="tests.ielts.score", value=7.5,
        source_type="document", confidence=0.6,
    )
    # document outranks conversation despite the lower confidence number.
    assert vault.get_fact(workspace.id, "tests.ielts.score").value["value"] == 7.5


def test_retract_hides_fact_from_reads_but_keeps_the_row(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    assert vault.retract_fact(workspace.id, "education.cgpa") == 1

    assert vault.get_fact(workspace.id, "education.cgpa") is None
    assert "education.cgpa" not in vault.snapshot(workspace.id)
    assert len(vault.history(workspace.id, "education.cgpa")) == 1


def test_multi_cardinality_replaces_the_whole_set(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Canada", "Germany"], source_type="user_explicit",
    )
    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Germany"], source_type="user_explicit",
    )
    active = vault.get_facts(workspace.id, "preferences.countries")
    assert len(active) == 1
    assert active[0].value["value"] == ["Germany"]


# ---------------------------------------------------------------------------
# Cardinality invariant: exactly ONE active row per key, for every cardinality
# ---------------------------------------------------------------------------

def test_list_field_reads_back_flat_not_nested(db_session, workspace, seed_fields):
    """Regression: snapshot() used to wrap a list-valued fact in another list.

    `preferences.countries` must read back as ["Germany", "Canada"], not
    [["Germany", "Canada"]] — the latter breaks every consumer and would have
    shipped a nested array into prompt context.
    """
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Germany", "Canada"], source_type="user_explicit",
    )
    assert vault.snapshot(workspace.id)["preferences.countries"] == ["Germany", "Canada"]


def test_one_active_row_per_key_for_list_fields(db_session, workspace, seed_fields):
    """The documented invariant: a list lives in ONE row, not one row per item."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Germany", "Canada", "Netherlands"], source_type="user_explicit",
    )
    active = vault.get_facts(workspace.id, "preferences.countries")
    assert len(active) == 1
    assert active[0].value["value"] == ["Germany", "Canada", "Netherlands"]


def test_list_field_ordering_is_preserved(db_session, workspace, seed_fields):
    """These lists are ranked — "most preferred first" must survive a round trip."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Germany", "Canada"], source_type="user_explicit",
    )
    assert vault.snapshot(workspace.id)["preferences.countries"] == ["Germany", "Canada"]

    vault.apply_fact(
        workspace_id=workspace.id, field_key="preferences.countries",
        value=["Canada", "Germany"], source_type="user_explicit",
    )
    assert vault.snapshot(workspace.id)["preferences.countries"] == ["Canada", "Germany"]


# ---------------------------------------------------------------------------
# Write outcomes
# ---------------------------------------------------------------------------

def test_outcomes_distinguish_created_from_superseded(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    first = vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=7.0,
        source_type="document",
    )
    assert first.outcome is VaultOutcome.CREATED and first.changed

    second = vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.0,
        source_type="user_explicit",
    )
    assert second.outcome is VaultOutcome.SUPERSEDED and second.changed


def test_losing_a_conflict_reports_retained_not_success(db_session, workspace, seed_fields):
    """A weaker source must be told its value did NOT become canonical."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    result = vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=6.0,
        source_type="conversation", confidence=0.9,
    )

    assert result.outcome is VaultOutcome.RETAINED
    assert not result.changed
    assert result.fact.value["value"] == 8.1       # the retained fact
    assert len(vault.history(workspace.id, "education.cgpa")) == 1


def test_manual_review_blocks_inference_even_with_no_existing_fact(db_session, workspace):
    """A review-gated field must park the FIRST value too, not just updates.

    Regression: the policy was only consulted when a prior fact existed, so
    the very first inferred value slipped straight into the Vault.
    """
    fields = VaultFieldDefinitionService(db_session)
    fields.upsert_definition({
        "key": "immigration.visa_refusals",
        "category": "immigration",
        "data_type": "integer",
        "validation_schema": {"type": "integer", "minimum": 0},
        "conflict_policy": "manual_review",
    })

    vault = VaultService(db_session)
    result = vault.apply_fact(
        workspace_id=workspace.id, field_key="immigration.visa_refusals",
        value=2, source_type="conversation", confidence=0.95,
    )

    assert result.outcome is VaultOutcome.NEEDS_REVIEW
    assert not result.changed
    assert vault.get_fact(workspace.id, "immigration.visa_refusals") is None


def test_manual_review_allows_an_explicit_user_statement(db_session, workspace):
    """The gate is on inference, not on the student telling us directly."""
    fields = VaultFieldDefinitionService(db_session)
    fields.upsert_definition({
        "key": "immigration.visa_refusals",
        "category": "immigration",
        "data_type": "integer",
        "validation_schema": {"type": "integer", "minimum": 0},
        "conflict_policy": "manual_review",
    })

    vault = VaultService(db_session)
    result = vault.apply_fact(
        workspace_id=workspace.id, field_key="immigration.visa_refusals",
        value=1, source_type="user_explicit",
    )

    assert result.outcome is VaultOutcome.CREATED
    assert vault.get_fact(workspace.id, "immigration.visa_refusals").value["value"] == 1


# ---------------------------------------------------------------------------
# Sensitivity and isolation
# ---------------------------------------------------------------------------

def test_sensitive_fields_are_withheld_from_default_snapshot(db_session, workspace, seed_fields):
    """finance.budget is marked sensitive, so it stays out of prompt context."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="finance.budget",
        value={"amount": 30000, "currency": "EUR"}, source_type="user_explicit",
    )
    assert "finance.budget" not in vault.snapshot(workspace.id)
    assert "finance.budget" in vault.snapshot(workspace.id, include_sensitive=True)


def test_vault_facts_are_isolated_between_workspaces(
    db_session, workspace, other_workspace, seed_fields,
):
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    vault.apply_fact(
        workspace_id=other_workspace.id, field_key="education.cgpa", value=5.0,
        source_type="user_explicit",
    )

    assert vault.get_fact(workspace.id, "education.cgpa").value["value"] == 8.1
    assert vault.get_fact(other_workspace.id, "education.cgpa").value["value"] == 5.0
    assert vault.snapshot(workspace.id)["education.cgpa"] == 8.1
