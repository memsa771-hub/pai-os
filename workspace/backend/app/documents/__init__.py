# -*- coding: utf-8 -*-
"""Student-document intelligence.

A document is another evidence channel for learning about the student. It
converges on the SAME canonical state as conversation:

    upload -> parse -> classify -> extract -> MemoryCandidate -> MemoryReconciler
                                                                      |
                                              Vault / typed records / memory

Nothing in this package writes canonical student state. Extraction proposes
candidates; `app.memory.reconciler` remains the only thing that decides.
"""

from .types import (
    DOCUMENT_CONTENT_TYPES,
    DOCUMENT_EXTENSIONS,
    DocumentTypeError,
    detect_document_type,
    is_student_document_candidate,
    sniff_content_type,
)

__all__ = [
    "DOCUMENT_CONTENT_TYPES",
    "DOCUMENT_EXTENSIONS",
    "DocumentTypeError",
    "detect_document_type",
    "is_student_document_candidate",
    "sniff_content_type",
]
