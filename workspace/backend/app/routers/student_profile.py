"""Student-facing profile contract over canonical Vault and typed records.

The Profile surface reads through `StudentProfileView` — a projection, not a
store — and writes through the ordinary candidate/reconciler path, so a
correction typed into the Profile page carries the same provenance and lands
in the same canonical records as one PAI extracted from a conversation.
"""
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.memory.candidates import MemoryCandidateService
from app.memory.errors import MemoryDataError
from app.memory.onboarding import OnboardingService
from app.memory.profile_completion import ProfileCompletionService
from app.memory.profile_issues import ProfileIssueService
from app.memory.readiness import ReadinessService, STAGES
from app.memory.reconciler import MemoryReconciler
from app.memory.student_profile_view import StudentProfileView
from app.memory.student_records import ENTITY_MODELS, StudentRecordService
from app.memory.vault import VaultService
from app.models import User
from app.response import ResponseCode, json_response, success_response
from app.routers.network import _resolve_workspace, _verify_workspace_access

router = APIRouter(prefix="/v1/student-profile", tags=["Student Profile"])


class ProfileEdit(BaseModel):
    record_type: Optional[str] = None
    record_id: Optional[str] = None
    field_key: Optional[str] = None
    value: Any
    reason: str = Field(min_length=1, max_length=500)


class IssueResolution(BaseModel):
    action: Literal["keep_current", "accept_proposed", "provide_new"]
    value: Any = None
    note: Optional[str] = Field(default=None, max_length=1000)


class OnboardingAnswers(BaseModel):
    """Free-form by design: the field set lives in ONBOARDING_FIELDS, so adding
    a question is a change in one place rather than a schema edit here too."""
    answers: dict[str, Any] = Field(default_factory=dict)


def _workspace(db, network, token, authorization):
    workspace = _resolve_workspace(db, network)
    if workspace is None:
        return None, json_response(ResponseCode.NOT_FOUND, "Network not found")
    if not _verify_workspace_access(workspace, token, authorization):
        return None, json_response(ResponseCode.UNAUTHORIZED, "Invalid credentials")
    return workspace, None


def _account(db: Session, workspace) -> dict:
    """The owning account's display identity — name, picture, login email.

    Account identity stays owned by the account system (see
    /v1/account/profile, which is also where the Profile page writes these
    back). It is read here so the Profile can render its header in one round
    trip instead of making the client stitch two responses together.
    """
    owner_id = getattr(workspace, "owner_user_id", None)
    if not owner_id:
        return {}
    user = db.execute(select(User).where(User.id == owner_id)).scalar_one_or_none()
    if user is None:
        return {}
    return {"displayName": user.display_name, "avatarUrl": user.avatar_url,
            "email": user.email}


@router.get("")
def get_student_profile(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """The Profile projection: account identity, safe facts, typed records."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    view = StudentProfileView(db).build(str(workspace.id), _account(db, workspace))
    return success_response(view)


@router.get("/raw")
def get_raw_student_profile(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """Unprojected canonical state, including restricted facts.

    Kept apart from the Profile surface deliberately: this is the operator and
    export view, and the browser Profile page must not call it.
    """
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    workspace_id = str(workspace.id)
    records = StudentRecordService(db)
    return success_response({
        "facts": VaultService(db).snapshot(workspace_id, include_sensitive=True),
        "records": records.snapshot(workspace_id),
        "issues": [{"id": i.id, "candidate_id": i.candidate_id,
                    "type": i.issue_type, "severity": i.severity,
                    "summary": i.summary, "clarification_question": i.clarification_question,
                    "status": i.status, "evidence": i.evidence, "resolution": i.resolution}
                   for i in records.issues(workspace_id)],
        "readiness": {stage: ReadinessService(db).evaluate(workspace_id, stage) for stage in STAGES},
    })


@router.get("/completion")
def get_profile_completion(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """Safe completion counts and the next useful question; never raw values."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    return success_response(ProfileCompletionService(db).evaluate(str(workspace.id)))


@router.get("/history")
def get_record_history(
    record_type: str = Query(...), record_id: str = Query(...),
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """The revision trail behind one record — who claimed what, and when."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    if record_type not in ENTITY_MODELS:
        return json_response(ResponseCode.BAD_REQUEST, "Unsupported record type")
    revisions = StudentRecordService(db).history(str(workspace.id), record_type, record_id)
    return success_response({"revisions": [
        {"id": r.id, "before": r.before, "after": r.after, "sourceType": r.source_type,
         "claimOrigin": r.claim_origin, "captureMethod": r.capture_method,
         "createdAt": r.created_at.isoformat() if r.created_at else None}
        for r in revisions]})


@router.post("/edits")
def edit_student_profile(
    edit: ProfileEdit, network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """A student's own correction — the canonical, validated write path."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    if bool(edit.record_type) == bool(edit.field_key):
        return json_response(ResponseCode.BAD_REQUEST, "Choose exactly one record_type or field_key")
    if edit.record_type and edit.record_type not in ENTITY_MODELS:
        return json_response(ResponseCode.BAD_REQUEST, "Unsupported record type")
    candidate = MemoryCandidateService(db).propose(
        workspace_id=str(workspace.id),
        candidate_type="student_record" if edit.record_type else "vault_fact",
        key=edit.record_type or edit.field_key, proposed_value=edit.value,
        entities={"record_id": edit.record_id} if edit.record_id else {},
        confidence=1.0, source_type="user_explicit", allow_user_explicit=True,
        evidence={"reason": edit.reason, "capture": "profile_edit"},
    )
    result = MemoryReconciler(db).reconcile(candidate)
    db.commit()
    if not result.accepted:
        return json_response(ResponseCode.BAD_REQUEST, result.reason or "Profile edit rejected")
    return success_response({"saved": True, "id": result.result_id})


@router.post("/issues/{issue_id}/resolve")
def resolve_profile_issue(
    issue_id: str, resolution: IssueResolution, network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    try:
        result = ProfileIssueService(db).resolve(
            str(workspace.id), issue_id, resolution.action,
            value=resolution.value, note=resolution.note,
        )
    except MemoryDataError as exc:
        db.rollback()
        return json_response(ResponseCode.BAD_REQUEST, str(exc))
    db.commit()
    if not result["resolved"]:
        return json_response(ResponseCode.BAD_REQUEST, result.get("reason") or "Resolution rejected")
    return success_response(result)


# ---------------------------------------------------------------------------
# First-run onboarding
#
# Separate endpoints rather than a flag on the profile payload: the gate runs
# before the workspace UI mounts, and it must not depend on the whole profile
# projection being buildable for an account that has no facts yet.
# ---------------------------------------------------------------------------


@router.get("/onboarding")
def get_onboarding(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """Whether the first-run form is still owed, and anything already known."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    return success_response(OnboardingService(db).state(workspace))


@router.post("/onboarding")
def submit_onboarding(
    body: OnboardingAnswers, network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """Write the student's answers canonically and mark onboarding done."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    try:
        result = OnboardingService(db).apply(workspace, body.answers)
    except MemoryDataError as exc:
        db.rollback()
        return json_response(ResponseCode.BAD_REQUEST, str(exc))
    db.commit()
    return success_response(result)


@router.post("/onboarding/skip")
def skip_onboarding(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    """Dismiss the form. PAI will learn the same facts through conversation."""
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    result = OnboardingService(db).skip(workspace)
    db.commit()
    return success_response(result)
