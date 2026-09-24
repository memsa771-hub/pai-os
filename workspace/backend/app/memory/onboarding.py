# -*- coding: utf-8 -*-
"""First-run onboarding — the student's opening statement about themselves.

What this exists for: PAI Counselor starts every new account knowing nothing.
A handful of stable facts asked once, up front, mean the very first reply can
already use their name, where they are, and whether they are studying or
working — instead of spending the first three turns collecting it.

Two design rules this module holds:

1. **It is not a second write path.** Every answer goes through the same
   MemoryCandidate -> MemoryReconciler -> Vault route that conversation
   extraction uses. Onboarding is just a caller with `source_type`
   `user_explicit`, so its facts carry provenance and land in history like
   any other claim. There is no onboarding table and no onboarding copy of a
   student's identity.

2. **It never writes a field PAI needs room to refine.** The status question
   is a coarse category and is stored as `identity.status_category`, NOT as
   `identity.current_status`. Under `latest_wins` a `user_explicit` fact can
   never be superseded by conversation extraction, so putting "Student" into
   `current_status` would permanently block PAI from ever learning
   "Final-year BS Computer Science student". See migration 066.

`user_explicit` also matters for a second reason: `identity.date_of_birth`
carries the `manual_review` conflict policy, and that policy admits exactly
one source — a first-hand statement. Asking the student directly is that
statement, so the answer applies immediately instead of parking as an issue.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import User

from .candidates import MemoryCandidateService
from .errors import MemoryDataError
from .reconciler import MemoryReconciler
from .vault import VaultService


@dataclass(frozen=True)
class OnboardingField:
    """One question, and the canonical Vault key its answer becomes."""
    name: str
    field_key: str
    required: bool = False
    choices: tuple[str, ...] = ()
    max_length: int = 200
    # YYYY, YYYY-MM or YYYY-MM-DD, matching the rest of the student schema.
    is_date: bool = False


# The order here is the order the form asks them in.
ONBOARDING_FIELDS: tuple[OnboardingField, ...] = (
    OnboardingField("fullName", "identity.full_name", required=True),
    OnboardingField("preferredName", "identity.preferred_name", required=True),
    OnboardingField(
        "statusCategory", "identity.status_category", required=True,
        choices=("student", "professional", "other"),
    ),
    OnboardingField("nationality", "identity.nationality", required=True),
    OnboardingField(
        "gender", "identity.gender", required=True,
        choices=("male", "female", "other", "undisclosed"),
    ),
    OnboardingField("dateOfBirth", "identity.date_of_birth", required=True, is_date=True),
    OnboardingField("currentCountry", "location.current_country", required=True),
    OnboardingField("currentCity", "location.current_city", required=True),
)

FIELDS_BY_NAME = {field.name: field for field in ONBOARDING_FIELDS}


def _clean(value: Any, field: OnboardingField) -> Optional[str]:
    """Validate one answer into a storable string, or None to skip it.

    Only shape is checked here. The field definition in the Vault is still the
    authority — this is the early, friendlier rejection so the form can say
    which box is wrong instead of surfacing a reconciler error.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryDataError(f"{field.name} must be text")
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > field.max_length:
        raise MemoryDataError(f"{field.name} is too long")
    if field.choices and text.casefold() not in field.choices:
        raise MemoryDataError(f"{field.name} must be one of: {', '.join(field.choices)}")
    if field.choices:
        text = text.casefold()
    if field.is_date:
        import re
        if not re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", text):
            raise MemoryDataError(f"{field.name} must be YYYY, YYYY-MM or YYYY-MM-DD")
        from datetime import date
        try:
            parsed = date.fromisoformat(
                text + ("-01-01" if len(text) == 4 else "-01" if len(text) == 7 else ""))
        except ValueError as exc:
            raise MemoryDataError(f"{field.name} is not a real date") from exc
        if parsed > date.today():
            raise MemoryDataError(f"{field.name} cannot be in the future")
    return text


class OnboardingService:
    """Read the onboarding state, and apply the student's answers."""

    def __init__(self, db):
        self.db = db
        self.vault = VaultService(db)

    # -- state -------------------------------------------------------------

    def owner(self, workspace) -> Optional[User]:
        owner_id = getattr(workspace, "owner_user_id", None)
        if not owner_id:
            return None
        return self.db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()

    def state(self, workspace) -> dict:
        """Whether the form is still owed, plus anything already known.

        Prefill matters: a student who comes back to a half-finished profile,
        or who was created before this flow existed, should see their own
        values rather than an empty form.
        """
        user = self.owner(workspace)
        facts = self.vault.snapshot(str(workspace.id), include_sensitive=True)
        prefill = {
            field.name: facts[field.field_key]
            for field in ONBOARDING_FIELDS
            if isinstance(facts.get(field.field_key), str)
        }
        # Full name is deliberately collected here, not at signup, and must
        # never be guessed from an account handle or OAuth display name.
        # Preferred name starts as the signup username but remains editable.
        if user is not None:
            prefill.setdefault("preferredName", user.username or "")
        return {
            "required": user is not None and user.onboarded_at is None,
            "completedAt": user.onboarded_at.isoformat()
            if user is not None and user.onboarded_at else None,
            "fields": [
                {"name": f.name, "fieldKey": f.field_key, "required": f.required,
                 "choices": list(f.choices), "isDate": f.is_date}
                for f in ONBOARDING_FIELDS
            ],
            "prefill": {k: v for k, v in prefill.items() if v},
        }

    # -- writes ------------------------------------------------------------

    def apply(self, workspace, answers: dict, *, complete: bool = True) -> dict:
        """Write the answers canonically and stamp the account as onboarded.

        Answers are validated as a set BEFORE anything is written, so a bad
        date never leaves half a profile behind.
        """
        if not isinstance(answers, dict):
            raise MemoryDataError("Onboarding answers must be an object")
        unknown = set(answers) - set(FIELDS_BY_NAME)
        if unknown:
            raise MemoryDataError(f"Unknown onboarding field: {', '.join(sorted(unknown))}")

        cleaned: dict[str, str] = {}
        for name, field in FIELDS_BY_NAME.items():
            value = _clean(answers.get(name), field)
            if value is not None:
                cleaned[name] = value
        missing = [f.name for f in ONBOARDING_FIELDS if f.required and f.name not in cleaned]
        if missing:
            raise MemoryDataError(f"Missing required answer: {', '.join(missing)}")

        workspace_id = str(workspace.id)
        user = self.owner(workspace)
        saved, rejected = [], {}
        existing_facts = self.vault.snapshot(workspace_id, include_sensitive=True)
        for name, value in cleaned.items():
            field = FIELDS_BY_NAME[name]
            # A returning student's prefilled canonical answer is already
            # satisfied. Do not supersede it with an identical onboarding row
            # or erase its original provenance merely because they continued.
            if existing_facts.get(field.field_key) == value:
                saved.append(field.field_key)
                continue
            candidate = MemoryCandidateService(self.db).propose(
                workspace_id=workspace_id, candidate_type="vault_fact",
                key=field.field_key, proposed_value=value, confidence=1.0,
                source_type="user_explicit", allow_user_explicit=True,
                subject_user_id=str(user.id) if user else None,
                evidence={"reason": "Stated during onboarding", "capture": "onboarding"},
            )
            result = MemoryReconciler(self.db).reconcile(candidate)
            if result.accepted:
                saved.append(field.field_key)
            else:
                # Never silently swallow it: the student typed this, and a
                # rejection means canonical state did not move.
                rejected[field.field_key] = result.reason or "rejected"

        if user is not None:
            # Completion is a canonical-state guarantee, not merely a form
            # submission stamp. Any reconciliation rejection keeps the gate
            # in place so the UI cannot enter Counselor with missing identity.
            if complete and not rejected:
                # Account/workspace surfaces still read this row. Mirror the
                # successfully reconciled canonical preferred name so the
                # student's chosen name is consistent everywhere immediately.
                user.display_name = cleaned["preferredName"]
                if user.onboarded_at is None:
                    user.onboarded_at = datetime.now(timezone.utc)

        return {"saved": saved, "rejected": rejected,
                "completed": bool(user and user.onboarded_at)}

