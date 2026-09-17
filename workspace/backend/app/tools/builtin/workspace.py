async def list_agents(ctx, _args):
    res = await ctx.api.get("/v1/discover", network=ctx.workspace_id)
    if not res["ok"]:
        return res
    agents = []
    for item in (res.get("data") or {}).get("agents") or []:
        skills = item.get("enabled_skills") or {}
        agents.append({
            "name": (item.get("address") or "").removeprefix("openagents:"),
            "type": item.get("agent_type"), "status": item.get("status"),
            "builtin": bool(item.get("builtin")), "description": item.get("description"),
            "installed_skills": skills.get("installed") or [],
        })
    return {"ok": True, "agents": agents}


async def list_threads(ctx, _args):
    res = await ctx.api.get("/v1/discover", network=ctx.workspace_id)
    if not res["ok"]:
        return res
    return {"ok": True, "threads": [{
        "name": (item.get("address") or "").removeprefix("channel/"),
        "title": item.get("title"), "leader": item.get("master"),
    } for item in (res.get("data") or {}).get("channels") or []]}


async def create_thread(ctx, args):
    """Create a thread as the calling agent.

    Goes through `emit_internal_event` rather than POST /v1/events. Public
    ingress now derives identity from credentials, and a cloud agent like PAI
    Operator has no join session to present — it is server-side code, so it
    takes the server-side path instead of trying to authenticate to its own
    API as if it were a client.
    """
    from app.database import new_session
    from app.event_identity import emit_internal_event
    from app.models import Workspace

    title = args["title"].strip() or "New thread"
    participants = [item for item in args.get("agents", []) if item]

    db = new_session()
    try:
        workspace = db.get(Workspace, ctx.workspace_id)
        if workspace is None:
            return {"ok": False, "error": {"code": "not_found", "message": "Workspace not found"}}
        result = await emit_internal_event(
            db, workspace,
            type="network.channel.create",
            source=ctx.source,
            target="core",
            payload={"title": title, "participants": participants},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": {"code": "execution_failed", "message": str(exc)[:200]}}
    finally:
        db.close()

    metadata = getattr(result, "metadata", None) or {}
    return {"ok": True, "channel_name": metadata.get("channel_name"), "title": title}
