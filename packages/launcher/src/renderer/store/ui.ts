import { create } from 'zustand'

interface ActivityEntry {
  time: string
  msg: string
}

interface UiState {
  // Active tab — replaces legacy _currentTab
  currentTab: string
  setCurrentTab: (tab: string) => void

  // Deep-link request for a page's own "create" dialog, so the dashboard's
  // "New agent" / "Create workspace" buttons open the real flow instead of just
  // dropping the user on the page. Consumed once and cleared by the page, which
  // keeps a later visit to that tab from re-opening the dialog.
  pendingCreate: 'agent' | 'workspace' | null
  requestCreate: (what: 'agent' | 'workspace') => void
  clearPendingCreate: () => void

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
  // Held here rather than inside the banner because clicking the launcher's
  // update notification has to bring a dismissed banner back — the banner
  // itself is unmounted by then, so component state could never be reached.
  updateBannerDismissed: string | null
  dismissUpdateBanner: (key: string) => void
  showUpdateBanner: () => void

  // Activity log — replaces legacy activityEntries[]
  activityLog: ActivityEntry[]
  addActivity: (msg: string) => void

  // Guided spotlight tour (new-user orientation over the real UI).
  tourOpen: boolean
  startTour: () => void
  endTour: () => void
}

export const useUiStore = create<UiState>((set) => ({
  currentTab: 'dashboard',
  // The Agents page has no rail entry of its own: This Computer lists the
  // agents, so anything still asking for it — a notification, a saved deep
  // link — lands there and the rail shows where the user is.
  setCurrentTab: (tab) => set({ currentTab: tab === 'agents' ? 'dashboard' : tab }),

  pendingCreate: null,
  requestCreate: (what) =>
    set({
      currentTab: what === 'agent' ? 'dashboard' : 'workspaces',
      pendingCreate: what,
    }),
  clearPendingCreate: () => set({ pendingCreate: null }),

  settingsSection: null,
  settingsSectionSignal: 0,
  openSettingsSection: (section) =>
    set((s) => ({
      currentTab: 'settings',
      settingsSection: section,
      settingsSectionSignal: s.settingsSectionSignal + 1,
    })),

  visibleSettingsSection: null,
  setVisibleSettingsSection: (section) =>
    set({ visibleSettingsSection: section }),

  updateBannerDismissed: null,
  dismissUpdateBanner: (key) => set({ updateBannerDismissed: key }),
  showUpdateBanner: () => set({ updateBannerDismissed: null }),

  activityLog: [],
  addActivity: (msg) => {
    const now = new Date()
    const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    set((state) => ({
      activityLog: [{ time, msg }, ...state.activityLog].slice(0, 50),
    }))
  },

  tourOpen: false,
  startTour: () => set({ tourOpen: true }),
  endTour: () => set({ tourOpen: false }),
}))
