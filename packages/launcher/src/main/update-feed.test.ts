import { describe, expect, it } from "vitest"
import { DEFAULT_LAUNCHER_FEED, hasLauncherFeed, launcherFeedUrl } from "./mirror"

// Placement AI Desktop used to check https://dl.openagents.org/launcher/stable
// on every launch — a DIFFERENT product's release feed. It downloaded
// OpenAgents-Launcher-<version>-win-x64.exe and reported itself ready to
// install, i.e. it would have replaced Placement AI on the user's machine.
// Placement AI publishes no feed yet, so self-update stays off until it does.
describe("launcher self-update feed", () => {
  it("is empty unless a Placement AI feed is configured", () => {
    expect(DEFAULT_LAUNCHER_FEED).toBe(process.env.PAI_LAUNCHER_FEED || "")
  })

  it("never points at an OpenAgents host", () => {
    expect(DEFAULT_LAUNCHER_FEED).not.toContain("openagents")
  })

  it("reports no feed, which is what disables the updater", () => {
    // updater.ts emits `supported: false` and returns when this is false; that
    // flag already guards check, download, install and applyUpdateFeedUrl.
    expect(hasLauncherFeed()).toBe(Boolean(process.env.PAI_LAUNCHER_FEED))
  })

  it("still accepts an explicit override when one is given", () => {
    expect(launcherFeedUrl("https://releases.example.invalid/stable"))
      .toBe("https://releases.example.invalid/stable")
    expect(launcherFeedUrl("not-a-url")).toBeNull()
    expect(launcherFeedUrl("ftp://releases.example.invalid")).toBeNull()
    expect(launcherFeedUrl("")).toBeNull()
    expect(launcherFeedUrl(undefined)).toBeNull()
  })
})
