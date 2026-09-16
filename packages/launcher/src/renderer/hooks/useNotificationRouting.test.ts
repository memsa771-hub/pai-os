import { describe, it, expect, beforeEach } from "vitest"

import { useUiStore } from "@renderer/store/ui"
import type { NotifRecord } from "@renderer/types"

import { canRouteNotification, routeNotification } from "./useNotificationRouting"

function notif(over: Partial<NotifRecord> = {}): NotifRecord {
  return {
    id: "n1",
    createdAt: "2026-08-06T00:00:00.000Z",
    read: false,
    kind: "update_available",
    title: "t",
    body: "b",
    ...over,
  }
}

/** Whatever the previous case navigated to must not leak into the next one. */
beforeEach(() => {
  useUiStore.setState({
    settingsOpen: false,
    settingsSection: null,
    settingsSectionSignal: 0,
    updateBannerDismissed: "downloaded:1.0.0",
  })
})

describe("routeNotification", () => {
  it("opens a Settings section", () => {
    expect(
      routeNotification(notif({ payload: { settingsSection: "updates" } })),
    ).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(true)
    expect(useUiStore.getState().settingsSection).toBe("updates")
  })

  // The banner is unmounted once dismissed, so the click has to bring it back
  // before navigating or the user lands somewhere with no way to install.
  it("restores a dismissed update banner for launcher updates", () => {
    routeNotification(
      notif({ source: "launcher-update", payload: { settingsSection: "updates" } }),
    )
    expect(useUiStore.getState().updateBannerDismissed).toBeNull()
  })

  it("reports going nowhere for an informational entry", () => {
    expect(routeNotification(notif({ kind: "system" }))).toBe(false)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })
})

describe("canRouteNotification", () => {
  it("agrees with routeNotification about what leads somewhere", () => {
    const cases: Array<[NotifRecord, boolean]> = [
      [notif({ payload: { settingsSection: "updates" } }), true],
      [notif({ source: "launcher-update" }), true],
      [notif({ kind: "system" }), false],
      [notif({ payload: {} }), false],
    ]
    for (const [record, expected] of cases) {
      expect(canRouteNotification(record)).toBe(expected)
    }
  })
})
