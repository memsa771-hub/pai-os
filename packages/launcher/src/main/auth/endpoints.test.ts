import { describe, expect, it } from "vitest"

import { apiBase, webBase, DEFAULT_API_BASE, DEFAULT_WEB_BASE } from "./endpoints"

describe("endpoints", () => {
  it("defaults to the hosted workspace API", () => {
    expect(apiBase(undefined)).toBe(DEFAULT_API_BASE)
  })

  it("defaults the web origin to the product's public address", () => {
    expect(webBase(undefined)).toBe(DEFAULT_WEB_BASE)
  })

  it("assumes a self-hosted endpoint serves both from one origin", () => {
    expect(webBase("https://oa.internal")).toBe("https://oa.internal")
    expect(webBase("https://workspace-endpoint.example.com")).toBe(
      "https://workspace-endpoint.example.com",
    )
  })

  it("drops a trailing slash so paths concatenate cleanly", () => {
    expect(apiBase("https://oa.internal/")).toBe("https://oa.internal")
  })
})

describe("webBase override", () => {
  it("wins over the derivation, for a front end served apart from its API", () => {
    process.env.PAI_WEB_BASE_OVERRIDE = "http://localhost:3001/"
    try {
      expect(webBase(undefined)).toBe("http://localhost:3001")
    } finally {
      delete process.env.PAI_WEB_BASE_OVERRIDE
    }
  })
})
