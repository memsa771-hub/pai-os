async def list_tasks(ctx, _args):
    res = await ctx.api.get("/v1/tasks", network=ctx.workspace_id)
    if not res["ok"]:
        return res
    return {"ok": True, "tasks": [{
        "id": item.get("id"), "title": item.get("title"), "status": item.get("status"),
        "priority": item.get("priority"), "assignee": item.get("assignee"),
    } for item in (res.get("data") or {}).get("tasks") or []]}


async def create_task(ctx, args):
    payload = {
        "network": ctx.workspace_id, "title": args["title"].strip(),
        "description": args.get("description", "").strip(),
        "priority": args.get("priority", "normal"), "source": ctx.source,
    }
    if args.get("assignee"):
        payload["assignee"] = args["assignee"]
    res = await ctx.api.post("/v1/tasks", json=payload)
    if not res["ok"]:
        return res
    item = res.get("data") or {}
    return {"ok": True, "task_id": item.get("id"), "title": item.get("title"), "status": item.get("status")}
