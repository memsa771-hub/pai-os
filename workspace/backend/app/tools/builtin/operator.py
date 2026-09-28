"""Tool handlers that delegate to PAI Operator (app/services/operator.py).

Thin wrappers on purpose — the actual execution loop, ExecutionRun state, and
background scheduling all live in the service module. This file only adapts
the (ctx, args) tool-call shape to it.
"""


async def delegate(ctx, args):
    from app.services import operator

    return await operator.delegate(
        ctx,
        args.get("objective", ""),
        args.get("constraints"),
        args.get("context_refs"),
        args.get("intent"),
        args.get("task_type"),
    )


async def status(ctx, args):
    from app.services import operator

    return await operator.get_status(ctx, args.get("run_id"))


async def resume(ctx, args):
    from app.services import operator

    return await operator.resume(ctx, args.get("run_id", ""), args.get("action") or {})
