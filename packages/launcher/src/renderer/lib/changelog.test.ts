import { describe, expect, it } from "vitest"
import { RELEASES, localized, releaseFor } from "./changelog"

describe("bundled release notes", () => {
  it("ships at least one release, newest first", () => {
    expect(RELEASES.length).toBeGreaterThan(0)
    for (let index = 1; index < RELEASES.length; index++) {
      expect(RELEASES[index - 1].version).not.toBe(RELEASES[index].version)
    }
  })

  it("carries English text for every entry", () => {
    for (const release of RELEASES) {
      expect(release.date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      for (const entry of release.entries) {
        expect(entry.title.en.trim()).not.toBe("")
        if (entry.description) expect(entry.description.en.trim()).not.toBe("")
      }
    }
  })

  it("finds a release by version", () => {
    const { version } = RELEASES[0]
    expect(releaseFor(version)?.version).toBe(version)
    expect(releaseFor(`v${version}`)?.version).toBe(version)
    expect(releaseFor("0.0.1")).toBeNull()
    expect(releaseFor(null)).toBeNull()
  })
})

describe("localized", () => {
  it("returns the shipped English text", () => {
    expect(localized({ en: "English" }, "en")).toBe("English")
  })
})
