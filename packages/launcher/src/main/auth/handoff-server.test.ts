import { describe, expect, it, afterEach } from "vitest"

import { startHandoffServer, type Handoff } from "./handoff-server"

/**
 * The loopback listener is the one part of the sign-in that is exposed to
 * whatever else runs on the machine, so what it refuses matters as much as
 * what it accepts.
 */

const ORIGIN = "https://workspace.openagents.org"

let open: Handoff | null = null

afterEach(() => {
  open?.close()
  open = null
})

async function post(
  handoff: Handoff,
  body: unknown,
): Promise<{ status: number }> {
  const res = await fetch(`http://127.0.0.1:${handoff.port}/desktop-auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  return { status: res.status }
}

describe("startHandoffServer", () => {
  it("resolves with the code posted for its own state", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))

    const [{ status }, result] = await Promise.all([
      post(handoff, { state: handoff.state, code: "auth-code-1" }),
      handoff.result,
    ])

    expect(status).toBe(200)
    expect(result.code).toBe("auth-code-1")
  })

  it("rejects a payload carrying someone else's state", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))
    const { status } = await post(handoff, {
      state: "not-the-state",
      code: "auth-code-1",
    })
    expect(status).toBe(400)
  })

  it("forwards a browser-side error", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))
    const [, result] = await Promise.all([
      post(handoff, { state: handoff.state, error: "access_denied" }),
      handoff.result,
    ])
    expect(result.error).toBe("access_denied")
  })

  it("accepts the query-string fallback for a browser that cannot POST", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))
    const query = new URLSearchParams({
      state: handoff.state,
      code: "auth-code-1",
    })
    const [, result] = await Promise.all([
      fetch(`http://127.0.0.1:${handoff.port}/desktop-auth?${query}`),
      handoff.result,
    ])
    expect(result.code).toBe("auth-code-1")
  })

  it("names only the workspace origin as allowed to read its replies", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))
    const res = await fetch(`http://127.0.0.1:${handoff.port}/desktop-auth`, {
      method: "OPTIONS",
    })
    expect(res.headers.get("access-control-allow-origin")).toBe(ORIGIN)
  })

  it("stops listening once the handoff is through", async () => {
    const handoff = (open = await startHandoffServer(ORIGIN))
    await Promise.all([
      post(handoff, { state: handoff.state, code: "one" }),
      handoff.result,
    ])
    // The port is given back as soon as the reply is on the wire; a second
    // attempt has nothing to talk to.
    await expect(post(handoff, { state: handoff.state, code: "two" })).rejects.toThrow()
  })
})
