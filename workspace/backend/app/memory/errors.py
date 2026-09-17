# -*- coding: utf-8 -*-
"""Memory domain errors.

The one distinction that matters here:

    MemoryDataError   a statement about the CANDIDATE — "this value is not
                      valid for this field". Deterministic, caller-caused, and
                      correctly recorded as a rejection.

    anything else     a statement about the SYSTEM — a bug, a dead database, a
                      typo'd attribute. Says nothing about the candidate, so it
                      must propagate and let the durable job retry.

Collapsing the two is how a NameError becomes a permanent "your CGPA was
rejected" on a student's record.
"""


class MemoryDataError(ValueError):
    """Base for expected, data-caused rejections."""
