async def list_tabs(ctx, _args):
    return await ctx.api.get("/v1/browser/tabs", network=ctx.workspace_id)


async def open_tab(ctx, args):
    return await ctx.api.post("/v1/browser/tabs", json={
        "network": ctx.workspace_id, "source": ctx.source,
        "url": args.get("url", "about:blank"), "context_id": args.get("context_id"),
    })


async def navigate(ctx, args):
    return await ctx.api.post(f"/v1/browser/tabs/{args['tab_id']}/navigate", json={"url": args["url"]})


async def read(ctx, args):
    return await ctx.api.get_text(f"/v1/browser/tabs/{args['tab_id']}/snapshot", max_chars=args.get("max_chars", 30000))


async def click(ctx, args):
    return await ctx.api.post(f"/v1/browser/tabs/{args['tab_id']}/click", json={"selector": args["selector"]})


async def type_text(ctx, args):
    return await ctx.api.post(f"/v1/browser/tabs/{args['tab_id']}/type", json={
        "selector": args["selector"], "text": args["text"], "append": args.get("append", False),
    })


async def screenshot(ctx, args):
    return await ctx.api.get_base64(f"/v1/browser/tabs/{args['tab_id']}/screenshot", max_bytes=2 * 1024 * 1024)


async def close(ctx, args):
    return await ctx.api.delete(f"/v1/browser/tabs/{args['tab_id']}")


async def list_contexts(ctx, _args):
    return await ctx.api.get("/v1/browser/contexts", network=ctx.workspace_id)
