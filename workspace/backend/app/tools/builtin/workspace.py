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
    title = args["title"].strip() or "New thread"
    participants = [item for item in args.get("agents", []) if item]
    res = await ctx.api.post("/v1/events", json={
        "type": "network.channel.create", "source": ctx.source, "target": "core",
        "payload": {"title": title, "participants": participants}, "metadata": {},
        "network": ctx.workspace_id,
    })
    if not res["ok"]:
        return res
    metadata = (res.get("data") or {}).get("metadata") or {}
    return {"ok": True, "channel_name": metadata.get("channel_name"), "title": title}
