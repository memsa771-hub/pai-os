import base64


async def list_files(ctx, args):
    return await ctx.api.get(
        "/v1/files/browse", network=ctx.workspace_id,
        folder=args.get("path", ""), recursive=args.get("recursive", False),
        limit=args.get("limit", 50),
    )


async def read_file(ctx, args):
    """Read a file's TEXT.

    PDF/DOCX go through the document pipeline's parsed content — handing a
    model `response.text` of a PDF produces mojibake it will confidently
    misread as content. Anything that is not a student document (a .txt, a
    .md, source code) still reads as raw text, so this stays a general file
    reader rather than a documents-only tool.

    A document that is still processing returns that state explicitly instead
    of empty text, so an agent can say "not ready yet" rather than invent
    findings.
    """
    file_id = args["file_id"]
    max_chars = args.get("max_chars", 50000)

    params = {"max_chars": max_chars}
    if args.get("pages"):
        params["pages"] = args["pages"]
    parsed = await ctx.api.get(
        f"/v1/files/{file_id}/content", network=ctx.workspace_id, **params,
    )

    data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    if isinstance(data, dict):
        if data.get("ok"):
            return data
        # Not a parsed document. `not_a_document` means no document pipeline
        # applies (a .txt, a code file) — fall through to the raw text read.
        # Every other reason is a real state the caller must see.
        if data.get("reason") != "not_a_document":
            return data

    return await ctx.api.get_text("/v1/files/" + file_id, max_chars=max_chars)


async def write_file(ctx, args):
    content = args["content"].encode("utf-8")
    return await ctx.api.post("/v1/files/base64", actor=ctx.source, json={
        "network": ctx.workspace_id, "filename": args["filename"],
        "content_base64": base64.b64encode(content).decode("ascii"),
        "content_type": args.get("content_type", "text/plain; charset=utf-8"),
        "channel_name": ctx.conversation,
    })
