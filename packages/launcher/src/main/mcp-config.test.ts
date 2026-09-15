import { describe, it, expect, beforeEach, afterEach } from "vitest"
import fs from "fs"
import os from "os"
import path from "path"

import {
  applyMcpServer,
  removeMcpServer,
  listMcpTargets,
  type McpTarget,
} from "./mcp-config"

const SECRET = "lin_api_test_key"

let dir: string
let targets: McpTarget[]

/** Generic injectable clients keep the MCP writer independently testable. */
function scratchTargets(root: string): McpTarget[] {
  return [
    {
      id: "local-client",
      label: "Local Client",
      file: path.join(root, "local-client", "mcp.json"),
      entry: (s) => ({ url: s.url, ...(s.headers ? { headers: s.headers } : {}) }),
    },
    {
      id: "typed-client",
      label: "Typed Client",
      file: path.join(root, "typed-client", "config.json"),
      entry: (s) => ({ type: "http", url: s.url, ...(s.headers ? { headers: s.headers } : {}) }),
    },
  ]
}

const fileFor = (id: string): string => targets.find((t) => t.id === id)!.file
const readFile = (id: string): Record<string, any> =>
  JSON.parse(fs.readFileSync(fileFor(id), "utf-8"))

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "oa-mcp-"))
  targets = scratchTargets(dir)
})

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

describe("applyMcpServer", () => {
  it("writes each client's own entry shape for the same endpoint", () => {
    const res = applyMcpServer("linear", SECRET, ["local-client", "typed-client"], targets)
    expect(res).toMatchObject({ ok: true, errors: [] })
    expect(res.written.sort()).toEqual(["local-client", "typed-client"])

    // Claude Code needs an explicit transport tag.
    expect(readFile("typed-client").mcpServers.linear).toEqual({
      type: "http",
      url: "https://mcp.linear.app/mcp",
      headers: { Authorization: `Bearer ${SECRET}` },
    })
    // Cursor infers the transport from `url`.
    expect(readFile("local-client").mcpServers.linear).toEqual({
      url: "https://mcp.linear.app/mcp",
      headers: { Authorization: `Bearer ${SECRET}` },
    })
  })

  it("preserves unrelated keys and other servers, and backs the file up once", () => {
    const file = fileFor("typed-client")
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(
      file,
      JSON.stringify({
        numStartups: 42,
        projects: { "/tmp/x": { allowedTools: [] } },
        mcpServers: { other: { type: "http", url: "https://example.com/mcp" } },
      }),
    )

    applyMcpServer("linear", SECRET, ["typed-client"], targets)

    const after = readFile("typed-client")
    expect(after.numStartups).toBe(42)
    expect(after.projects).toEqual({ "/tmp/x": { allowedTools: [] } })
    expect(after.mcpServers.other).toEqual({ type: "http", url: "https://example.com/mcp" })
    expect(after.mcpServers.linear.url).toBe("https://mcp.linear.app/mcp")

    // The backup captures the pre-modification state...
    const backup = `${file}.openagents.bak`
    expect(JSON.parse(fs.readFileSync(backup, "utf-8")).mcpServers.linear).toBeUndefined()

    // ...and a second write must not overwrite that original snapshot.
    applyMcpServer("linear", "second_key", ["typed-client"], targets)
    expect(JSON.parse(fs.readFileSync(backup, "utf-8")).mcpServers.linear).toBeUndefined()
    expect(readFile("typed-client").mcpServers.linear.headers.Authorization).toBe("Bearer second_key")
  })

  it("refuses to clobber a config it cannot parse", () => {
    const file = fileFor("typed-client")
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, "{ not json ")

    const res = applyMcpServer("linear", SECRET, ["typed-client"], targets)
    expect(res.ok).toBe(false)
    expect(res.written).toEqual([])
    expect(res.errors[0]).toContain("Typed Client")
    // Original bytes untouched.
    expect(fs.readFileSync(file, "utf-8")).toBe("{ not json ")
  })

  it("rejects platforms with no known MCP endpoint", () => {
    const res = applyMcpServer("telegram", SECRET, ["typed-client"], targets)
    expect(res.ok).toBe(false)
    expect(res.errors[0]).toContain("telegram")
    expect(fs.existsSync(fileFor("typed-client"))).toBe(false)
  })
})

describe("removeMcpServer", () => {
  it("drops only this platform's entry", () => {
    const file = fileFor("local-client")
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, JSON.stringify({ mcpServers: { other: { url: "https://x/mcp" } } }))
    applyMcpServer("linear", SECRET, ["local-client"], targets)

    const res = removeMcpServer("linear", ["local-client"], targets)
    expect(res).toMatchObject({ ok: true, written: ["local-client"] })
    expect(readFile("local-client").mcpServers).toEqual({ other: { url: "https://x/mcp" } })
  })

  it("is a no-op when the file or entry is absent", () => {
    expect(removeMcpServer("linear", ["typed-client"], targets)).toMatchObject({
      ok: true,
      written: [],
    })
  })
})

describe("listMcpTargets", () => {
  it("reports detection, configured state, and parse errors", () => {
    applyMcpServer("linear", SECRET, ["typed-client"], targets)
    const bad = fileFor("local-client")
    fs.mkdirSync(path.dirname(bad), { recursive: true })
    fs.writeFileSync(bad, "nope")

    const byId = Object.fromEntries(
      listMcpTargets("linear", targets).map((s) => [s.id, s]),
    )
    expect(byId["typed-client"]).toMatchObject({ detected: true, configured: true })
    expect(byId["local-client"].error).toBeTruthy()
    expect(byId["local-client"].configured).toBe(false)
  })

  it("reports nothing as configured for a platform with no MCP endpoint", () => {
    applyMcpServer("linear", SECRET, ["typed-client"], targets)
    expect(listMcpTargets("telegram", targets).every((s) => !s.configured)).toBe(true)
  })
})
