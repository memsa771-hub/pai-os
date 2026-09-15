import { useEffect } from "react"
import { useAccountStore } from "@renderer/store/account"

import { useNotificationsStore } from "@renderer/store/notifications"
import { useUiStore } from "@renderer/store/ui"
import type { NotifRecord } from "@renderer/types"

/**
 * Where a notification leads, as the main process describes it when pushing.
 *
 *   settingsSection  a Settings sub-page
 *   tab              a plain page
 *
 * `unknown` rather than `string`: this arrives over IPC from a store on disk,
 * so every field is checked before it is used.
 */
interface NotificationRoute {
  tab?: unknown
  settingsSection?: unknown
}

/**
 * Whether clicking this notification would go anywhere — the same question
 * routeNotification answers, minus the navigating. Surfaces that decorate a
 * row as clickable have to know before the click, and asking here keeps that
 * decision from drifting away from the routing itself.
 */
export function canRouteNotification(record: NotifRecord): boolean {
  const payload = (record.payload ?? {}) as NotificationRoute
  return (
    record.source === "launcher-update" ||
    typeof payload.settingsSection === "string" ||
    typeof payload.tab === "string"
  )
}

/**
 * What clicking a notification does — shared by the OS toast, the rail's bell
 * and the dashboard's activity card, so no two of them can disagree about where
 * a given notification leads.
 *
 * Returns whether the click led anywhere, so callers can close a popover only
 * when something actually happened.
 */
export function routeNotification(record: NotifRecord): boolean {
  const payload = (record.payload ?? {}) as NotificationRoute
  if (canRouteNotification(record)) useAccountStore.getState().exitWorkspace()
  const ui = useUiStore.getState()
  let acted = false

  // A dismissed update banner is unmounted, so re-showing it has to happen
  // before navigation — otherwise clicking the "update ready" toast lands the
  // user on a page with no visible way to install.
  if (record.source === "launcher-update") {
    ui.showUpdateBanner()
    acted = true
  }

  if (typeof payload.settingsSection === "string") {
    ui.openSettingsSection(payload.settingsSection)
    return true
  }

  if (typeof payload.tab === "string") {
    ui.setCurrentTab(payload.tab)
    return true
  }
  return acted
}

/**
 * Handles clicks on the OS-level toast. Main focuses the window and forwards
 * the record; without this subscription the click did nothing beyond raising
 * the window, whatever the notification said.
 */
export function useNotificationClicks(): void {
  useEffect(() => {
    return window.api.onNotificationClicked((record) => {
      if (!record.read) void useNotificationsStore.getState().markRead(record.id)
      routeNotification(record)
    })
  }, [])
}
