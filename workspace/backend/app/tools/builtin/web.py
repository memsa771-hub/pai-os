from app.tools.web_search import get_web_search_provider


async def fetch(ctx, args):
    return await ctx.api.post("/v1/fetch", actor=ctx.source, json={
        "network": ctx.workspace_id,
        "url": args["url"], "mode": args.get("mode", "auto"),
        "max_chars": args.get("max_chars", 20000),
    })


async def search(ctx, args):
    provider = get_web_search_provider()
    if provider is None:
        return {"ok": False, "error": {"code": "search_not_configured", "message": "Web search is not configured"}}
    results = await provider.search(args["query"], args.get("limit", 5))
    return {"ok": True, "results": results}
