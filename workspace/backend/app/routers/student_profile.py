"""Student-facing profile contract over canonical Vault and typed records."""
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.memory.candidates import MemoryCandidateService
from app.memory.readiness import ReadinessService, STAGES
from app.memory.reconciler import MemoryReconciler
from app.memory.student_records import ENTITY_MODELS, StudentRecordService
from app.memory.vault import VaultService
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
    note: str = Field(min_length=1, max_length=1000)


def _workspace(db, network, token, authorization):
    workspace = _resolve_workspace(db, network)
    if workspace is None:
        return None, json_response(ResponseCode.NOT_FOUND, "Network not found")
    if not _verify_workspace_access(workspace, token, authorization):
        return None, json_response(ResponseCode.UNAUTHORIZED, "Invalid credentials")
    return workspace, None


@router.get("")
def get_student_profile(
    network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
    workspace, error = _workspace(db, network, x_workspace_token, authorization)
    if error:
        return error
    workspace_id = str(workspace.id)
    records = StudentRecordService(db)
    return success_response({
        "facts": VaultService(db).snapshot(workspace_id, include_sensitive=True),
        "records": records.snapshot(workspace_id),
        "issues": [{"id": i.id, "type": i.issue_type, "severity": i.severity,
                    "summary": i.summary, "clarification_question": i.clarification_question,
                    "status": i.status, "evidence": i.evidence, "resolution": i.resolution}
                   for i in records.issues(workspace_id)],
        "readiness": {stage: ReadinessService(db).evaluate(workspace_id, stage) for stage in STAGES},
    })


@router.post("/edits")
def edit_student_profile(
    edit: ProfileEdit, network: str = Query(...), db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None),
):
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
    issue = next((i for i in StudentRecordService(db).issues(str(workspace.id)) if i.id == issue_id), None)
    if issue is None:
        return json_response(ResponseCode.NOT_FOUND, "Profile issue not found")
    issue.status = "resolved"
    issue.resolution = {"note": resolution.note, "resolved_by": "student"}
    issue.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return success_response({"resolved": True})
