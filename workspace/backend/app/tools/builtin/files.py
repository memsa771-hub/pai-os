import base64


async def list_files(ctx, args):
    return await ctx.api.get(
        "/v1/files/browse", network=ctx.workspace_id,
        folder=args.get("path", ""), recursive=args.get("recursive", False),
        limit=args.get("limit", 50),
    )


async def read_file(ctx, args):
    return await ctx.api.get_text("/v1/files/" + args["file_id"], max_chars=args.get("max_chars", 50000))


async def write_file(ctx, args):
    content = args["content"].encode("utf-8")
    return await ctx.api.post("/v1/files/base64", actor=ctx.source, json={
        "network": ctx.workspace_id, "filename": args["filename"],
        "content_base64": base64.b64encode(content).decode("ascii"),
        "content_type": args.get("content_type", "text/plain; charset=utf-8"),
        "channel_name": ctx.conversation,
    })
