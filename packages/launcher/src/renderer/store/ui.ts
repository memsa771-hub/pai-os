import { create } from 'zustand'

interface UiState {
  // Whether the app-shell Settings screen is on top of the Workspace. There is
  // no other screen left to switch between — Settings is a small overlay, not
  // a nav tab.
  settingsOpen: boolean
  openSettings: () => void
  closeSettings: () => void

  // Deep-link into a specific Settings section (used by the update banner to
  // drop the user straight on Settings → Updates). The signal is bumped on
  // every call so re-requesting the section the user already navigated away
  // from still re-selects it.
  settingsSection: string | null
  settingsSectionSignal: number
  openSettingsSection: (section: string) => void

  // Which Settings module is actually on screen right now, or null when
  // Settings is not. Distinct from `settingsSection` above, which is only ever
  // a request: it keeps whatever was last deep-linked even after the user
  // navigates away, so it cannot answer "what is the user looking at". The
  // update banner needs that answer — it hides itself on Settings → Updates,
  // where the same state is already on the page in full.
  visibleSettingsSection: string | null
  setVisibleSettingsSection: (section: string | null) => void

  // Which update-banner state the user waved away, as "<status>:<version>".
  // Held here rather than inside the banner because clicking the app's update
  // notification has to bring a dismissed banner back — the banner itself is
  // unmounted by then, so component state could never be reached.
  updateBannerDismissed: string | null
  dismissUpdateBanner: (key: string) => void
  showUpdateBanner: () => void
}

export const useUiStore = create<UiState>((set) => ({
  settingsOpen: false,
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false, settingsSection: null }),

  settingsSection: null,
  settingsSectionSignal: 0,
  openSettingsSection: (section) =>
    set((s) => ({
      settingsOpen: true,
      settingsSection: section,
      settingsSectionSignal: s.settingsSectionSignal + 1,
    })),

  visibleSettingsSection: null,
  setVisibleSettingsSection: (section) =>
    set({ visibleSettingsSection: section }),

  updateBannerDismissed: null,
  dismissUpdateBanner: (key) => set({ updateBannerDismissed: key }),
  showUpdateBanner: () => set({ updateBannerDismissed: null }),
}))
