# -*- coding: utf-8 -*-
"""
PAI Operator — Placement AI's hidden execution intelligence.

PAI Counselor (see ``services/pai.py``) is the student's only conversational
agent. PAI Operator is what Counselor delegates real work to when a request
needs more than a reply — research, filling a draft, running a multi-step
plan through the browser/docs/tasks tools. It is never a WorkspaceMember and
never a CloudAgentConfig row: it has no channel, no membership, nothing that
``/v1/discover`` or the ``workspace.agents.list`` tool could ever surface, so
it is structurally impossible for it to appear in any agent picker or roster
— not a UI filter someone could forget to apply, an absence of the row those
filters read from.

Provisioning is therefore implicit: Operator's model/credentials are the same
server-managed ``config.PAI_*`` values PAI Counselor already uses (see
``pai.resolve_credentials``/``pai.resolve_model``) — nothing to seed per
workspace, nothing to backfill, nothing to migrate if PAI is disabled.

Counselor delegates to it through two ordinary tools in the SAME shared tool
runtime (``operator.delegate`` / ``operator.status`` — see
``app/tools/builtin/operator.py``), not a separate agent-to-agent protocol.
``delegate`` creates an ``ExecutionRun`` row and schedules the actual work as
a background asyncio task — chat is never blocked on it — and returns
immediately; ``status`` reads the row back so Counselor can answer "what's
the status of my application?" in a later turn without re-running anything.

Execution itself follows a real five-phase loop — UNDERSTAND, PLAN, EXECUTE,
OBSERVE, VERIFY — using the exact same ``ToolRegistry``/``ToolExecutor``/
``ToolPolicy`` as every other tool-calling agent: Operator's tool list is
whatever the registry currently holds (minus ``operator.*`` itself, so it
cannot delegate to itself), discovered at call time, not a hardcoded list —
so a future plugin's tools become available to it the moment they register,
with no code change here. SENSITIVE tools stay blocked by the shared
``ToolPolicy`` default exactly as they are for every other caller; nothing in
this module ever bypasses it.

Operator reads workspace state (files, tasks, threads) through the same
``pai.WorkspaceApi`` Counselor uses and keeps no memory of its own — the
"do not give Operator its own student memory" requirement is structural, not
a promise: there is nowhere on this module's state for one to live.
"""

import asyncio
import json as _json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.config import config
from app.database import SessionLocal
from app.models import ExecutionRun
from app.services import pai
from app.services.cloud_providers import chat_completion, chat_completion_tools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Identity — mirrors pai.py's constants section. No PAI_AGENT_TYPE, no
# WorkspaceMember: see module docstring for why that's deliberate.
# ---------------------------------------------------------------------------

PAI_OPERATOR_AGENT_NAME = "pai-operator"
PAI_OPERATOR_DISPLAY_NAME = "PAI Operator"

# Terminal states a run can end in.
STATUS_COMPLETED = "completed"
STATUS_NEEDS_USER_ACTION = "needs_user_action"
STATUS_FAILED = "failed"
_TERMINAL = frozenset({STATUS_COMPLETED, STATUS_NEEDS_USER_ACTION, STATUS_FAILED})

OPERATOR_SYSTEM_PROMPT = """\
You are PAI Operator, Placement AI's internal execution intelligence.

You are never shown to the student directly and you never chat with them —
PAI Counselor is the only voice they hear. PAI Counselor delegates you a
concrete objective; your job is to actually get it done using the tools
available to you (browser, docs/files, tasks, workflows, web search, and
whatever else the tool registry currently exposes), then hand back a
factual, verified result for Counselor to relay in its own words.

Rules:
- Never claim something succeeded unless a tool result confirms it.
- Prefer the smallest number of real actions that make verifiable progress.
- If a step needs something only the student can provide or approve
  (submitting an application, sending something irreversible, a missing
  document), stop and say so — do not guess or act around it.
- You have no memory of your own. Everything you know about this student
  comes from the workspace state below and what PAI Counselor told you for
  this objective.
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_json(raw: str) -> Optional[str]:
    """Best-effort strip of markdown fences around a JSON payload the model
    was asked to return "with ONLY JSON" — instructions models don't always
    follow to the letter."""
    if not raw:
        return None
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return text or None


def _parse_json_list(raw: str) -> Optional[list]:
    text = _extract_json(raw)
    if not text:
        return None
    try:
        value = _json.loads(text)
    except Exception:
        return None
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value[:12]
    return None


def _parse_json_object(raw: str) -> dict:
    text = _extract_json(raw)
    if not text:
        return {}
    try:
        value = _json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def serialize_run(run: ExecutionRun) -> dict:
    """The shape both the frontend (GET /v1/operator/runs) and the
    operator.status tool return — one source of truth for it."""
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "requested_by": run.requested_by,
        "objective": run.objective,
        "constraints": run.constraints or {},
        "context_refs": run.context_refs or [],
        "status": run.status,
        "current_step": run.current_step,
        "plan": run.plan or [],
        "completed_steps": run.completed_steps or [],
        "missing": run.missing or [],
        "approval_required_for": run.approval_required_for,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _publish_run_updated(workspace_id: str, run: ExecutionRun) -> None:
    """Best-effort SSE nudge so the frontend can refetch instead of polling
    blind. Never allowed to fail the run it's reporting on."""
    try:
        from app import cache
        event = {
            "id": str(uuid.uuid4()),
            "type": "workspace.operator.run_updated",
            "source": f"openagents:{PAI_OPERATOR_AGENT_NAME}",
            "target": "core",
            "payload": serialize_run(run),
            "metadata": {},
            "timestamp": int(time.time() * 1000),
        }
        cache.publish_event(
            f"ws:{workspace_id}:events",
            _json.dumps(event, default=str, separators=(",", ":")).encode(),
        )
    except Exception:
        logger.exception("operator: failed to publish run update for %s", run.id)


async def _post_result(
    db: Any, workspace_id: str, channel_target: Optional[str], message: str,
) -> None:
    """Auto-post the finished run's outcome into the thread it was delegated
    from — the same event-pipeline path a normal cloud-agent reply already
    uses (see ``cloud_agent._post_response``), so the student sees the result
    the moment it's ready instead of having to ask "did it work?" in a later
    turn. Posted as PAI Counselor (``pai.PAI_AGENT_NAME``): Operator never
    speaks to the student directly, only Counselor does.

    Best-effort — a failure here must never surface as the run itself
    failing; the ExecutionRun row (already committed by the caller) remains
    the source of truth either way.
    """
    if not channel_target or not message:
        return
    try:
        from app.services.cloud_agent import _post_response
        db.rollback()
        await _post_response(db, workspace_id, channel_target, pai.PAI_AGENT_NAME, message, depth=0)
    except Exception:
        logger.exception("operator: failed to auto-post result to %s", channel_target)


def _terminal_message(status: str, summary: Optional[str], missing: Any, verification: dict) -> str:
    """Compose the chat message for a run's terminal state — what the
    student actually sees, in PAI Counselor's voice."""
    if status == STATUS_COMPLETED:
        return summary or "Done."
    if status == STATUS_NEEDS_USER_ACTION:
        parts = [summary] if summary else []
        approval = verification.get("approval_required_for")
        if approval:
            parts.append(f"I need your go-ahead before continuing: {approval}.")
        if isinstance(missing, list) and missing:
            parts.append("Still missing: " + ", ".join(str(m) for m in missing) + ".")
        return " ".join(p for p in parts if p) or "I need a bit more from you before I can finish this."
    return summary or "I ran into an issue and couldn't finish this — let me know if you'd like me to try again."


def is_available() -> bool:
    """Operator shares Counselor's server credentials — nothing to check
    beyond whether PAI itself is configured."""
    return bool(config.PAI_ENABLED and config.PAI_API_KEY)


# ---------------------------------------------------------------------------
# Tool-facing entry points — called from app/tools/builtin/operator.py
# ---------------------------------------------------------------------------

async def delegate(ctx, objective: str, constraints: Optional[dict], context_refs: Optional[list]) -> dict:
    """Create an ExecutionRun and schedule its execution in the background.

    Returns immediately — the caller (PAI Counselor's tool loop) gets an
    acknowledgement, not the finished result, so chat is never blocked on
    however long the actual work takes.
    """
    objective = (objective or "").strip()
    if not objective:
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "objective is required"}}
    if not is_available():
        return {"ok": False, "error": {"code": "operator_unavailable", "message": "PAI Operator is not configured on the server"}}

    # `ctx.conversation` is the same thread id PAI Counselor's own tool
    # context carries (see cloud_agent._invoke_assistant_agent) — reconstruct
    # the event target so the finished run can post its result back into the
    # exact thread the objective came from, the way any other agent reply does.
    channel_target = f"channel/{ctx.conversation}" if getattr(ctx, "conversation", None) else None

    db = SessionLocal()
    try:
        run = ExecutionRun(
            workspace_id=ctx.workspace_id,
            requested_by=ctx.source,
            channel_target=channel_target,
            objective=objective,
            constraints=constraints or {},
            context_refs=context_refs or [],
            status="pending",
            current_step="Queued",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        data = {"run_id": run_id, "status": run.status, "objective": objective}
    finally:
        db.close()

    # Fire-and-forget: the request/tool-call that got us here returns well
    # before this finishes. `ctx.api` is a stateless per-call HTTP client
    # (just workspace_id + token), safe to keep using from the background task.
    asyncio.create_task(
        _execute(run_id, ctx.workspace_id, ctx.api, objective, constraints or {}, context_refs or [], channel_target)
    )

    return {"ok": True, "data": data}


async def get_status(ctx, run_id: Optional[str]) -> dict:
    """Read an ExecutionRun back — how PAI Counselor answers "what's the
    status of X?" without re-running anything."""
    db = SessionLocal()
    try:
        query = select(ExecutionRun).where(ExecutionRun.workspace_id == ctx.workspace_id)
        if run_id:
            query = query.where(ExecutionRun.id == run_id)
        else:
            query = query.order_by(ExecutionRun.created_at.desc())
        run = db.execute(query.limit(1)).scalar_one_or_none()
        if not run:
            return {"ok": True, "data": {"status": "none"}}
        return {"ok": True, "data": serialize_run(run)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The execution loop: UNDERSTAND -> PLAN -> EXECUTE -> OBSERVE -> VERIFY
# ---------------------------------------------------------------------------

async def _execute(
    run_id: str, workspace_id: str, api: Any,
    objective: str, constraints: dict, context_refs: list,
    channel_target: Optional[str] = None,
) -> None:
    db = SessionLocal()
    try:
        run = db.get(ExecutionRun, run_id)
        if not run:
            logger.error("operator: run %s vanished before execution started", run_id)
            return

        def set_status(status: str, **fields: Any) -> None:
            run.status = status
            for key, value in fields.items():
                setattr(run, key, value)
            db.commit()
            db.refresh(run)
            _publish_run_updated(workspace_id, run)

        api_key = config.PAI_API_KEY
        provider = pai.PAI_PROVIDER
        model = config.PAI_MODEL
        base_url = config.PAI_BASE_URL or None

        try:
            state_summary = await pai.workspace_state_summary(api)
        except Exception:
            state_summary = "Current workspace state (live): (unavailable)"

        # ---- UNDERSTAND ----
        set_status("understanding", current_step="Understanding the objective")
        try:
            understanding = await chat_completion(
                api_key=api_key, provider=provider, model=model,
                messages=[{"role": "user", "content": (
                    f"{state_summary}\n\nObjective from PAI Counselor: {objective}\n"
                    f"Constraints: {constraints}\nContext references: {context_refs}\n\n"
                    "UNDERSTAND phase only. In 2-4 sentences, restate what is actually "
                    "being asked, note what is already known, and name the biggest "
                    "unknown. Do not plan or act yet."
                )}],
                system_prompt=OPERATOR_SYSTEM_PROMPT, max_tokens=400, base_url=base_url,
            )
        except Exception as exc:
            logger.exception("operator: understand phase failed for run %s", run_id)
            set_status(STATUS_FAILED, error=f"understand phase failed: {exc}"[:500], completed_at=_now())
            await _post_result(db, workspace_id, channel_target, "I ran into an issue starting on that — want me to try again?")
            return

        # ---- PLAN ----
        set_status("planning", current_step="Planning the steps")
        try:
            plan_raw = await chat_completion(
                api_key=api_key, provider=provider, model=model,
                messages=[{"role": "user", "content": (
                    f"Objective: {objective}\nUnderstanding: {understanding}\n\n"
                    "PLAN phase. Reply with ONLY a JSON array of 3-8 short step "
                    "labels (strings) — the concrete ordered steps needed, using "
                    "the tools you have (browser, docs/files, tasks, workflows, web "
                    "search). No prose, no markdown fences, just the JSON array."
                )}],
                system_prompt=OPERATOR_SYSTEM_PROMPT, max_tokens=400, base_url=base_url,
            )
        except Exception as exc:
            logger.exception("operator: plan phase failed for run %s", run_id)
            set_status(STATUS_FAILED, error=f"plan phase failed: {exc}"[:500], completed_at=_now())
            await _post_result(db, workspace_id, channel_target, "I ran into an issue starting on that — want me to try again?")
            return
        plan = _parse_json_list(plan_raw) or [objective]
        set_status("executing", plan=plan, current_step=plan[0])

        # ---- EXECUTE + OBSERVE ----
        from app.tools import ToolContext, get_tool_executor, get_tool_registry
        tool_registry = get_tool_registry()
        tool_executor = get_tool_executor()
        # Dynamic discovery: everything currently registered, minus
        # operator.* itself (no self-delegation). Never a hardcoded list —
        # a plugin's tools show up here the moment it registers them.
        allowed_tools = frozenset(
            t.name for t in tool_registry.all() if not t.name.startswith("operator.")
        )
        tools = tool_registry.openai_tools_for_agent(allowed_tools)
        tool_context = ToolContext(
            workspace_id=workspace_id, agent_name=PAI_OPERATOR_AGENT_NAME,
            api=api, allowed_tools=allowed_tools,
        )

        messages: list[dict] = [{"role": "user", "content": (
            f"Objective: {objective}\nPlan:\n" + "\n".join(f"- {s}" for s in plan) +
            "\n\nEXECUTE phase. Work through the plan using the available tools. "
            "Stop calling tools and reply in plain text as soon as you have gone as "
            "far as you safely can without a human's approval, or the objective is "
            "done."
        )}]
        system_prompt = OPERATOR_SYSTEM_PROMPT + "\n\n" + state_summary
        completed_steps: list[str] = []
        final_text = ""
        max_iters = max(1, config.PAI_MAX_TOOL_ITERATIONS)

        for i in range(max_iters):
            use_tools = tools if i < max_iters - 1 else None
            db.rollback()
            try:
                msg = await chat_completion_tools(
                    api_key=api_key, provider=provider, model=model,
                    messages=messages, tools=use_tools,
                    system_prompt=system_prompt, max_tokens=None, base_url=base_url,
                )
            except Exception as exc:
                logger.exception("operator: execute phase failed for run %s", run_id)
                set_status(STATUS_FAILED, error=f"execute phase failed: {exc}"[:500], completed_at=_now())
                await _post_result(db, workspace_id, channel_target, "I ran into an issue partway through that — want me to try again?")
                return

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                final_text = msg.get("content", "") or ""
                break

            messages.append(msg)
            for tc in tool_calls:
                tool_def = tool_registry.get(tc["function"]["name"])
                tool_name = tool_def.name if tool_def else tc["function"]["name"]
                try:
                    tool_args = _json.loads(tc["function"].get("arguments") or "{}")
                except Exception:
                    tool_args = {}
                # OBSERVE: the executor's own result IS the observation — did
                # the call actually succeed, not just "was it made".
                result = await tool_executor.execute(tool_name, tool_args, tool_context)
                if result.get("ok"):
                    completed_steps.append(tool_name)
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": _json.dumps(result, default=str)[:4000],
                })
            set_status(
                "executing", completed_steps=completed_steps,
                current_step=f"Ran {len(completed_steps)} action(s) so far",
            )

        # ---- VERIFY ----
        set_status("verifying", current_step="Verifying the result")
        try:
            verify_raw = await chat_completion(
                api_key=api_key, provider=provider, model=model,
                messages=[{"role": "user", "content": (
                    f"Objective: {objective}\n"
                    f"Final message: {final_text or '(stopped after the tool-call budget, no final summary)'}\n"
                    f"Actions actually taken: {completed_steps}\n\n"
                    'VERIFY phase. Reply with ONLY JSON: {"status": '
                    '"completed" | "needs_user_action" | "failed", "missing": '
                    '[short strings], "approval_required_for": short string or null, '
                    '"summary": "one short sentence, for the student, via PAI Counselor"}'
                )}],
                system_prompt=OPERATOR_SYSTEM_PROMPT, max_tokens=400, base_url=base_url,
            )
            verification = _parse_json_object(verify_raw)
        except Exception:
            logger.exception("operator: verify phase failed for run %s (non-fatal, falling back)", run_id)
            verification = {}

        status = verification.get("status")
        if status not in _TERMINAL:
            status = STATUS_COMPLETED if final_text else STATUS_NEEDS_USER_ACTION
        missing = verification.get("missing")
        summary = verification.get("summary") or final_text[:200] or None

        set_status(
            status,
            missing=missing if isinstance(missing, list) else [],
            approval_required_for=verification.get("approval_required_for"),
            current_step=summary,
            completed_at=_now(),
        )
        await _post_result(db, workspace_id, channel_target, _terminal_message(status, summary, missing, verification))
    except Exception as exc:
        logger.exception("operator: run %s failed unexpectedly", run_id)
        try:
            run = db.get(ExecutionRun, run_id)
            if run:
                run.status = STATUS_FAILED
                run.error = str(exc)[:500]
                run.completed_at = _now()
                db.commit()
                _publish_run_updated(workspace_id, run)
                await _post_result(db, workspace_id, channel_target, "I ran into an unexpected issue and had to stop working on that — want me to try again?")
        except Exception:
            logger.exception("operator: failed to record failure for run %s", run_id)
    finally:
        db.close()
