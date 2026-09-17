# -*- coding: utf-8 -*-
"""Synthetic labelled dataset for retrieval evaluation.

SYNTHETIC AND REPRESENTATIVE — never production student records. Everything
here is invented to exercise the retrieval shapes PAI actually needs.

Two workspaces on purpose: `ws-a` is the student under test and `ws-b` holds
deliberately similar content, so a leak shows up as a failed case rather than
passing silently.

The retrieval code must not be tuned against these strings. They exist to
measure generic behaviour — paraphrase matching, exact-identifier matching,
negative cases — not to be special-cased.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

# Real UUIDs — `workspaces.id` and the memory tables' `workspace_id` are UUID
# columns, so readable labels cannot be used directly. Deterministic (uuid5 on
# a fixed namespace) so a failing case names the same workspace on every run.
_EVAL_NS = uuid.UUID("2b5b0b3a-8f0a-4a7a-9d2e-6c1f0a4b7d31")
WS_A = str(uuid.uuid5(_EVAL_NS, "eval-workspace-a"))
WS_B = str(uuid.uuid5(_EVAL_NS, "eval-workspace-b"))


@dataclass(frozen=True)
class EvalMemory:
    id: str
    workspace_id: str
    content: str
    memory_type: str = "context"
    importance: float = 0.5
    # Forgotten rows must be indexed-then-deactivated to test staleness.
    forgotten: bool = False


@dataclass(frozen=True)
class EvalEpisode:
    id: str
    workspace_id: str
    summary: str
    event_type: str = "decision_made"
    importance: float = 0.5
    days_ago: int = 0
    forgotten: bool = False


@dataclass(frozen=True)
class EvalCase:
    """One labelled query."""

    name: str
    query: str
    # Ids that SHOULD be retrieved, best first.
    relevant: tuple[str, ...]
    # Ids that must NEVER appear (wrong value, wrong student, forgotten).
    forbidden: tuple[str, ...] = ()
    workspace_id: str = WS_A
    category: str = "general"
    # True when the case hinges on an exact token (number, proper noun, id).
    exact_term: bool = False


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

MEMORIES: tuple[EvalMemory, ...] = (
    # Test scores — near-identical text, different numbers.
    EvalMemory("m_ielts", WS_A, "Scored IELTS 7.5 overall with 7.0 in writing.",
               "context", 0.8),
    EvalMemory("m_gre", WS_A, "Scored GRE 322 with a quantitative score of 168.",
               "context", 0.7),
    EvalMemory("m_toefl", WS_A, "Took TOEFL earlier and scored 104.", "context", 0.5),

    # Destination preferences.
    EvalMemory("m_germany", WS_A, "Germany is the first-choice study destination.",
               "preference", 0.9),
    EvalMemory("m_netherlands", WS_A, "Open to the Netherlands as a second option.",
               "preference", 0.6),
    EvalMemory("m_not_us", WS_A, "Does not want to study in the United States.",
               "constraint", 0.7),

    # Finance.
    EvalMemory("m_budget", WS_A,
               "Cannot afford tuition above 20000 euros per year.", "constraint", 0.9),
    EvalMemory("m_scholarship", WS_A,
               "Scholarship availability is a major factor in every decision.",
               "constraint", 0.85),
    EvalMemory("m_family_support", WS_A,
               "Family can contribute about 8000 euros towards the first year.",
               "context", 0.6),

    # Academic interests.
    EvalMemory("m_ai_research", WS_A,
               "Main interest is artificial intelligence research, especially robotics.",
               "interest", 0.9),
    EvalMemory("m_nlp", WS_A, "Also curious about natural language processing.",
               "interest", 0.5),
    EvalMemory("m_no_finance", WS_A, "Not interested in finance or accounting programmes.",
               "constraint", 0.4),

    # Institutions and programmes — exact proper nouns.
    EvalMemory("m_tum", WS_A,
               "Shortlisted TU Munich for the MSc Informatics programme.", "goal", 0.8),
    EvalMemory("m_rwth", WS_A, "Also considering RWTH Aachen for robotics.", "goal", 0.7),
    EvalMemory("m_app_id", WS_A,
               "Application reference APP-2027-DE-4471 submitted to TU Munich.",
               "context", 0.75),

    # Career and timeline.
    EvalMemory("m_career", WS_A,
               "Wants to work as a research engineer after graduating.", "goal", 0.7),
    EvalMemory("m_intake", WS_A, "Targeting the Fall 2027 intake.", "goal", 0.85),
    EvalMemory("m_backlogs", WS_A, "Has two academic backlogs from second year.",
               "context", 0.6),

    # Forgotten — must never be retrieved.
    EvalMemory("m_canada_old", WS_A,
               "Canada was the preferred destination.", "preference", 0.8,
               forgotten=True),

    # Other student, deliberately similar wording.
    EvalMemory("m_b_ielts", WS_B, "Scored IELTS 7.5 overall in the second attempt.",
               "context", 0.8),
    EvalMemory("m_b_germany", WS_B, "Germany is the first-choice study destination.",
               "preference", 0.9),
    EvalMemory("m_b_tum", WS_B, "Shortlisted TU Munich for MSc Informatics.", "goal", 0.8),
)

EPISODES: tuple[EvalEpisode, ...] = (
    EvalEpisode("e_removed_x", WS_A,
                "Removed University X from the shortlist because tuition was too high.",
                "shortlist_removed", 0.8, days_ago=3),
    EvalEpisode("e_removed_y", WS_A,
                "Removed University Y because it had no robotics track.",
                "shortlist_removed", 0.6, days_ago=20),
    EvalEpisode("e_fall_2027", WS_A,
                "Decided to target the Fall 2027 intake rather than Spring.",
                "decision_made", 0.85, days_ago=1),
    EvalEpisode("e_uploaded", WS_A,
                "Uploaded the final degree transcript.", "document_uploaded", 0.5,
                days_ago=10),
    EvalEpisode("e_ielts_booked", WS_A,
                "Booked the IELTS retake for March.", "decision_made", 0.6, days_ago=45),
    EvalEpisode("e_advisor", WS_A,
                "Spoke to a university advisor about the robotics curriculum.",
                "decision_made", 0.5, days_ago=60),
    EvalEpisode("e_old_canada", WS_A,
                "Considered Canada and requested information.", "decision_made", 0.4,
                days_ago=200, forgotten=True),
    EvalEpisode("e_b_removed", WS_B,
                "Removed University X because tuition was too high.",
                "shortlist_removed", 0.8, days_ago=3),
)


# ---------------------------------------------------------------------------
# Labelled queries
# ---------------------------------------------------------------------------

CASES: tuple[EvalCase, ...] = (
    # -- exact identifiers / numbers ------------------------------------
    EvalCase("ielts_score", "What was my IELTS score?", ("m_ielts",),
             category="exact_term", exact_term=True),
    EvalCase("ielts_number", "IELTS 7.5", ("m_ielts",),
             forbidden=("m_b_ielts",), category="exact_term", exact_term=True),
    EvalCase("gre_score", "What did I get in the GRE?", ("m_gre",),
             category="exact_term", exact_term=True),
    EvalCase("toefl_score", "my TOEFL result", ("m_toefl",),
             category="exact_term", exact_term=True),
    EvalCase("app_reference", "APP-2027-DE-4471", ("m_app_id",),
             category="identifier", exact_term=True),
    EvalCase("application_ref_phrase", "what is my application reference number",
             ("m_app_id",), category="identifier", exact_term=True),
    EvalCase("tum_exact", "TU Munich", ("m_tum",), forbidden=("m_b_tum",),
             category="proper_noun", exact_term=True),
    EvalCase("rwth_exact", "RWTH Aachen", ("m_rwth",),
             category="proper_noun", exact_term=True),
    EvalCase("msc_informatics", "MSc Informatics programme", ("m_tum",),
             category="proper_noun", exact_term=True),

    # -- paraphrase / semantic ------------------------------------------
    EvalCase("country_preference", "Which country did I say I prefer?",
             ("m_germany",), category="paraphrase"),
    EvalCase("where_study", "where do I want to study abroad", ("m_germany",),
             category="paraphrase"),
    EvalCase("second_choice", "what was my backup country", ("m_netherlands",),
             category="paraphrase"),
    EvalCase("avoid_country", "which country do I want to avoid", ("m_not_us",),
             category="paraphrase"),
    EvalCase("budget_paraphrase", "how much can I spend on fees each year",
             ("m_budget",), category="paraphrase"),
    EvalCase("affordability", "what is my affordability limit", ("m_budget",),
             category="paraphrase"),
    EvalCase("scholarship_need", "do I need financial aid", ("m_scholarship",),
             category="paraphrase"),
    EvalCase("family_money", "how much will my family contribute",
             ("m_family_support",), category="paraphrase"),
    EvalCase("research_area", "what research field am I interested in",
             ("m_ai_research",), category="paraphrase"),
    EvalCase("robotics_interest", "am I interested in robotics", ("m_ai_research",),
             category="paraphrase"),
    EvalCase("language_tech", "do I like language technology", ("m_nlp",),
             category="paraphrase"),
    EvalCase("career_goal", "what job do I want after my degree", ("m_career",),
             category="paraphrase"),
    EvalCase("intake_target", "which intake am I aiming for",
             ("m_intake", "e_fall_2027"), category="paraphrase"),
    EvalCase("academic_issues", "do I have any failed subjects", ("m_backlogs",),
             category="paraphrase"),
    EvalCase("dislikes_subject", "which subjects do I not want to study",
             ("m_no_finance",), category="paraphrase"),

    # -- episodes -------------------------------------------------------
    EvalCase("why_removed_x", "Why did I remove University X?", ("e_removed_x",),
             category="episode"),
    EvalCase("why_removed_y", "why did I drop University Y", ("e_removed_y",),
             category="episode"),
    EvalCase("fall_2027_decision", "when did I decide on Fall 2027",
             ("e_fall_2027",), category="episode"),
    EvalCase("transcript_upload", "did I upload my transcript", ("e_uploaded",),
             category="episode"),
    EvalCase("ielts_retake", "when is my IELTS retake booked", ("e_ielts_booked",),
             category="episode"),
    EvalCase("advisor_conversation", "did I talk to an advisor", ("e_advisor",),
             category="episode"),
    EvalCase("recent_shortlist_change", "what did I recently remove from my shortlist",
             ("e_removed_x",), category="recency"),

    # -- mixed kinds ----------------------------------------------------
    EvalCase("tuition_concern", "tuition was too expensive",
             ("m_budget", "e_removed_x"), category="mixed"),
    EvalCase("germany_overall", "Germany", ("m_germany",),
             forbidden=("m_b_germany",), category="mixed", exact_term=True),

    # -- negative / isolation -------------------------------------------
    EvalCase("forgotten_canada", "Canada preference", (),
             forbidden=("m_canada_old", "e_old_canada"), category="forgotten"),
    EvalCase("forgotten_not_in_country_query", "which country do I prefer",
             ("m_germany",), forbidden=("m_canada_old",), category="forgotten"),
    EvalCase("cross_workspace_ielts", "IELTS 7.5", ("m_b_ielts",),
             forbidden=("m_ielts",), workspace_id=WS_B, category="isolation",
             exact_term=True),
    EvalCase("cross_workspace_tum", "TU Munich", ("m_b_tum",),
             forbidden=("m_tum",), workspace_id=WS_B, category="isolation",
             exact_term=True),
    EvalCase("cross_workspace_episode", "why did I remove University X",
             ("e_b_removed",), forbidden=("e_removed_x",), workspace_id=WS_B,
             category="isolation"),

    # -- near-miss values -----------------------------------------------
    EvalCase("gre_not_ielts", "GRE 322", ("m_gre",), forbidden=("m_ielts",),
             category="near_miss", exact_term=True),
    EvalCase("budget_number", "20000 euros per year", ("m_budget",),
             category="near_miss", exact_term=True),
)


def case_categories() -> tuple[str, ...]:
    return tuple(sorted({c.category for c in CASES}))
