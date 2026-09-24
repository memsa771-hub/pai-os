# -*- coding: utf-8 -*-
"""What KIND of document this is, and who issued it.

Two separate questions that are easy to conflate:

    classification  transcript, CV, SOP, offer letter, ...
    authority       who is making the claim

`source_type="document"` does NOT mean institution-issued. A CV is a document
the student wrote about themselves; a transcript is a registrar's assertion.
Treating both as "document" gave a self-authored CV the same weight as an
official transcript in conflict resolution, which is wrong in the direction
that matters — it lets an unverified claim outrank a verified one.

Authority is derived from the CLASSIFICATION, not from the presence of a
file_id, because "a file exists" says nothing about who wrote it.
"""

from __future__ import annotations

#: Document classes. `generic_student_document` is the honest fallback for a
#: real but unclassifiable student document; `unknown` means we could not
#: tell it relates to the student at all.
DOCUMENT_CLASSES = (
    "transcript",
    "degree_certificate",
    "school_certificate",
    "test_score_report",
    "cv_resume",
    "sop_personal_statement",
    "recommendation_letter",
    "passport_identity",
    "financial_document",
    "scholarship_document",
    "application_document",
    "offer_letter",
    "visa_document",
    "generic_student_document",
    "unknown",
)

#: Classes that do NOT describe the student's education/career journey and
#: should not become a StudentDocument profile record.
NON_JOURNEY_CLASSES = frozenset({"unknown"})

# Authority levels, ordered by how much independent verification they carry.
AUTHORITY_INSTITUTION = "institution_issued"
AUTHORITY_TEST_PROVIDER = "test_provider_issued"
AUTHORITY_GOVERNMENT = "government_issued"
AUTHORITY_THIRD_PARTY = "third_party_authored"
AUTHORITY_STUDENT = "student_authored"
AUTHORITY_AGENT = "agent_generated"
AUTHORITY_UNKNOWN = "unknown"

#: Classification -> who is asserting it.
CLASS_AUTHORITY = {
    "transcript": AUTHORITY_INSTITUTION,
    "degree_certificate": AUTHORITY_INSTITUTION,
    "school_certificate": AUTHORITY_INSTITUTION,
    "offer_letter": AUTHORITY_INSTITUTION,
    "test_score_report": AUTHORITY_TEST_PROVIDER,
    "passport_identity": AUTHORITY_GOVERNMENT,
    "visa_document": AUTHORITY_GOVERNMENT,
    "recommendation_letter": AUTHORITY_THIRD_PARTY,
    "cv_resume": AUTHORITY_STUDENT,
    "sop_personal_statement": AUTHORITY_STUDENT,
    "application_document": AUTHORITY_STUDENT,
    "financial_document": AUTHORITY_UNKNOWN,       # bank letter vs. own budget
    "scholarship_document": AUTHORITY_UNKNOWN,
    "generic_student_document": AUTHORITY_UNKNOWN,
    "unknown": AUTHORITY_UNKNOWN,
}

#: Verification status recorded on typed records, by authority. A record
#: backed by a registrar is materially different from one backed by a CV.
AUTHORITY_VERIFICATION = {
    AUTHORITY_INSTITUTION: "document_supported",
    AUTHORITY_TEST_PROVIDER: "document_supported",
    AUTHORITY_GOVERNMENT: "document_supported",
    AUTHORITY_THIRD_PARTY: "extracted",
    AUTHORITY_STUDENT: "self_reported",
    AUTHORITY_AGENT: "extracted",
    AUTHORITY_UNKNOWN: "extracted",
}


def authority_for(classification: str, uploaded_by: str = "") -> str:
    """Who is asserting the contents of this document.

    A file PAI itself produced is `agent_generated` regardless of what it
    looks like — a CV PAI drafted must never come back as evidence about the
    student, which would launder a suggestion into a fact.
    """
    if uploaded_by and not uploaded_by.startswith("human:"):
        return AUTHORITY_AGENT
    return CLASS_AUTHORITY.get(classification, AUTHORITY_UNKNOWN)


def verification_for(authority: str) -> str:
    return AUTHORITY_VERIFICATION.get(authority, "extracted")


def is_journey_document(classification: str) -> bool:
    """Whether this deserves a StudentDocument profile record.

    A random unrelated PDF should not pollute the student's document list.
    """
    return classification in DOCUMENT_CLASSES and classification not in NON_JOURNEY_CLASSES
