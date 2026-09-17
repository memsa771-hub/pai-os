# -*- coding: utf-8 -*-
"""The evaluation harness itself — metrics and dataset integrity.

The harness is code too: a silently wrong Recall calculation would make every
future quality claim meaningless. These run without Qdrant or a provider.
"""

import pytest

from app.memory.eval_dataset import CASES, EPISODES, MEMORIES, WS_A, WS_B
from app.memory.eval_retrieval import (
    CaseResult,
    EvalReport,
    hit_at_k,
    recall_at_k,
    reciprocal_rank,
)


# -- metrics ---------------------------------------------------------------

def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, 3) == 1.0
    assert recall_at_k(["a", "x", "y"], {"a", "b"}, 3) == 0.5
    assert recall_at_k(["x", "y", "z"], {"a"}, 3) == 0.0
    # k truncates.
    assert recall_at_k(["x", "x", "x", "a"], {"a"}, 3) == 0.0
    assert recall_at_k(["x", "x", "x", "a"], {"a"}, 5) == 1.0
    # No relevant ids -> not scored (pure negative case).
    assert recall_at_k(["a"], set(), 3) is None


def test_hit_at_k():
    assert hit_at_k(["x", "a"], {"a"}, 3) == 1.0
    assert hit_at_k(["x", "y"], {"a"}, 3) == 0.0
    assert hit_at_k(["x", "y", "z", "a"], {"a"}, 3) == 0.0
    assert hit_at_k(["a"], set(), 3) is None


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a"}) == 0.0
    assert reciprocal_rank(["a"], set()) is None


def _case(name="c", relevant=("a",), forbidden=(), category="general"):
    from app.memory.eval_dataset import EvalCase

    return EvalCase(name=name, query="q", relevant=relevant,
                    forbidden=forbidden, category=category)


def test_a_leak_fails_the_case_regardless_of_recall():
    """Retrieving another student's memory is never an acceptable trade."""
    result = CaseResult(
        case=_case(forbidden=("leaked",)), retrieved=["a", "leaked"], mode="hybrid",
        recall_5=1.0, hit_5=1.0, forbidden_hits=("leaked",),
    )
    assert not result.passed


def test_pure_negative_case_passes_when_nothing_returned():
    result = CaseResult(case=_case(relevant=()), retrieved=[], mode="hybrid")
    assert result.passed


def test_report_excludes_unscored_cases_from_means():
    """A negative case must not drag the average toward zero."""
    report = EvalReport(results=[
        CaseResult(case=_case(), retrieved=["a"], mode="hybrid",
                   recall_5=1.0, hit_5=1.0, mrr=1.0),
        CaseResult(case=_case(relevant=()), retrieved=[], mode="hybrid"),
    ])
    assert report.recall_5 == 1.0
    assert report.hit_5 == 1.0


def test_gates_fail_on_any_leak():
    report = EvalReport(results=[
        CaseResult(case=_case(forbidden=("x",)), retrieved=["x"], mode="hybrid",
                   recall_5=1.0, hit_5=1.0, forbidden_hits=("x",)),
    ])
    assert report.leaks
    assert not report.meets_gates(0.0, 0.0), "a leak must fail the gate outright"


def test_mode_counts_are_reported():
    report = EvalReport(results=[
        CaseResult(case=_case(), retrieved=[], mode="hybrid"),
        CaseResult(case=_case(), retrieved=[], mode="lexical_fallback"),
        CaseResult(case=_case(), retrieved=[], mode="hybrid"),
    ])
    assert report.mode_counts == {"hybrid": 2, "lexical_fallback": 1}


# -- dataset integrity ------------------------------------------------------

def test_dataset_is_large_enough():
    assert len(CASES) >= 30, "the labelled set should cover ~30-50 cases"


def test_every_referenced_id_exists():
    known = {m.id for m in MEMORIES} | {e.id for e in EPISODES}
    for case in CASES:
        for record_id in (*case.relevant, *case.forbidden):
            assert record_id in known, f"{case.name} references unknown id {record_id}"


def test_dataset_covers_the_required_categories():
    categories = {c.category for c in CASES}
    for required in (
        "exact_term", "identifier", "paraphrase", "episode",
        "isolation", "forgotten", "near_miss", "proper_noun",
    ):
        assert required in categories, f"missing coverage: {required}"


def test_dataset_has_two_workspaces_for_isolation():
    assert {m.workspace_id for m in MEMORIES} == {WS_A, WS_B}
    isolation = [c for c in CASES if c.category == "isolation"]
    assert isolation, "no isolation cases"
    # Each isolation case forbids the other workspace's near-identical row.
    assert all(c.forbidden for c in isolation)


def test_forgotten_rows_are_marked_and_referenced():
    forgotten = {m.id for m in MEMORIES if m.forgotten} | {
        e.id for e in EPISODES if e.forgotten
    }
    assert forgotten, "dataset needs forgotten rows to test staleness"
    forbidden_anywhere = {i for c in CASES for i in c.forbidden}
    assert forgotten & forbidden_anywhere, "forgotten rows are never asserted against"


def test_case_names_are_unique():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
