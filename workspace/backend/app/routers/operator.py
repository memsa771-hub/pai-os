# -*- coding: utf-8 -*-
"""
PAI Operator status endpoints — read-only visibility into ExecutionRun rows
for the frontend's "PAI is working…" indicator.

GET /v1/operator/runs          List runs for a workspace (most recent first)
GET /v1/operator/runs/{run_id} Read one run

There is no POST here: runs are only ever created by PAI Counselor's
operator.delegate tool call (see app/services/operator.py) — never directly
from the frontend, since Operator is not a feature a student invokes, it is
something PAI does. These endpoints exist purely so the UI can show that.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ExecutionRun
from app.response import ResponseCode, json_response, success_response
from app.routers.network import _resolve_workspace, _verify_workspace_access
from app.services.operator import serialize_run

router = APIRouter(prefix="/v1", tags=["Operator"])


@router.get("/operator/runs")
async def list_runs(
    network: str = Query(...),
    status: Optional[str] = Query(None, description="Filter to one status, or 'active' for pending/understanding/planning/executing/verifying"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    workspace = _resolve_workspace(db, network)
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Network not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid credentials")

    ws_id = str(workspace.id)
    query = select(ExecutionRun).where(ExecutionRun.workspace_id == ws_id)
    if status == "active":
        query = query.where(ExecutionRun.status.in_(
            ["pending", "understanding", "planning", "executing", "verifying"]
        ))
    elif status:
        query = query.where(ExecutionRun.status == status)
    query = query.order_by(ExecutionRun.created_at.desc()).limit(limit)
    rows = db.execute(query).scalars().all()

    return success_response({"runs": [serialize_run(r) for r in rows]})


@router.get("/operator/runs/{run_id}")
async def get_run(
    run_id: str = Path(...),
    network: str = Query(...),
    db: Session = Depends(get_db),
    x_workspace_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    workspace = _resolve_workspace(db, network)
    if not workspace:
        return json_response(ResponseCode.NOT_FOUND, "Network not found")
    if not _verify_workspace_access(workspace, x_workspace_token, authorization):
        return json_response(ResponseCode.UNAUTHORIZED, "Invalid credentials")

    run = db.execute(
        select(ExecutionRun).where(
            ExecutionRun.id == run_id,
            ExecutionRun.workspace_id == str(workspace.id),
        )
    ).scalar_one_or_none()
    if not run:
        return json_response(ResponseCode.NOT_FOUND, "Run not found")

    return success_response(serialize_run(run))
