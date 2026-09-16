import React, { useEffect } from "react"
import { useUiStore } from "./store/ui"
import { useThemeStore } from "./store/theme"
import { useAppearanceStore } from "./store/appearance"
import { useNotificationsStore } from "./store/notifications"
import { useAccountStore } from "./store/account"
import { TitleBar } from "./components/layout/title-bar"
import { Toaster } from "./components/ui/sonner"
import WorkspacePage from "./pages/workspace"
import Settings from "./pages/settings"
import { Spinner } from "./components/ui/spinner"
import { WhatsNewDialog } from "./components/whats-new/whats-new-dialog"
import { useWhatsNew } from "./components/whats-new/use-whats-new"
import { useToasts } from "./hooks/useToast"
import { useNotificationClicks } from "./hooks/useNotificationRouting"
import { useFullScreen } from "./hooks/useFullScreen"
import { capture } from "./lib/analytics"

/**
 * PAI Desktop is one screen: the account's Workspace, hosted full window.
 * Settings is a small overlay on top of it for OS-shell concerns (updates,
 * appearance, network) — never a second product surface. See store/ui.ts.
 */
export default function App(): React.JSX.Element {
  const initTheme = useThemeStore((s) => s.init)
  const initAppearance = useAppearanceStore((s) => s.init)
  const initNotifications = useNotificationsStore((s) => s.init)
  const initAccount = useAccountStore((s) => s.init)
  const { showToast } = useToasts()
  const whatsNew = useWhatsNew()
  const settingsOpen = useUiStore((s) => s.settingsOpen)

  useFullScreen()

  useEffect(() => {
    initTheme()
    initAppearance()
    void initNotifications()
    // Reads the stored session and subscribes to changes. Signed out is a
    // perfectly good outcome — the Workspace simply shows its own sign-in gate.
    void initAccount()
  }, [initTheme, initAppearance, initNotifications, initAccount])

  useNotificationClicks()

  const accountReady = useAccountStore((s) => s.ready)

  // Track in-app navigation as pageviews, mirroring website navigation.
  useEffect(() => {
    capture("$pageview", {
      screen: settingsOpen ? "settings" : "workspace",
      $current_url: `app://pai-desktop/${settingsOpen ? "settings" : "workspace"}`,
    })
  }, [settingsOpen])

  return (
    <>
      {/* Persistent native chrome above the Workspace or Settings. */}
      <div className="flex h-screen flex-col overflow-hidden">
        <TitleBar />
        <div className="min-h-0 flex-1">
          {!accountReady ? (
            <div className="flex h-full items-center justify-center"><Spinner className="size-5" /></div>
          ) : settingsOpen ? (
            <Settings showToast={showToast} />
          ) : (
            <WorkspacePage showToast={showToast} />
          )}
        </div>
      </div>

      <Toaster position="bottom-right" />

      {accountReady && (
        <WhatsNewDialog
          open={whatsNew.open}
          releases={whatsNew.releases}
          onClose={whatsNew.close}
        />
      )}
    </>
  )
}
