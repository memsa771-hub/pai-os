import React, { useEffect, useState } from "react"
import { useShallow } from "zustand/react/shallow"
import { useTranslation } from "react-i18next"
import { X } from "lucide-react"

import { ConfirmDialog } from "@renderer/components/ui-kit"
import { useUiStore } from "@renderer/store/ui"
import type { ToastType } from "@renderer/hooks/useToast"

import { DetailHeader } from "./components/detail-header"
import { RelatedSettings } from "./components/related-settings"
import { ResetSummary } from "./components/reset-summary"
import { SettingsOverview } from "./components/settings-overview"
import { RELATED, SECTIONS, type SectionId } from "./section-config"
import { useLauncherUpdater } from "./use-launcher-updater"
import { useSectionSummaries } from "./use-section-summaries"
import { useSettingsIO } from "./use-settings-io"
import { useSettingsState } from "./use-settings-state"
import { useSystemInfo } from "./use-system-info"
import { GeneralSection } from "./sections/general-section"
import { AppearanceSection } from "./sections/appearance-section"
import { PrivacySection } from "./sections/privacy-section"
import { AboutSection } from "./sections/about-section"

// Which lines each confirmation spells out; the copy lives under the matching
// `settings.*Dialog.affected/kept` i18n prefix.
const LOCAL_RESET_AFFECTED = ["appearance", "layout", "history"]
const LOCAL_RESET_KEPT = ["settings"]

interface SettingsProps {
  showToast: (msg: string, type?: ToastType) => void
}

export default function Settings({ showToast }: SettingsProps): React.JSX.Element {
  const { t } = useTranslation()
  const {
    values,
    update,
    runtimeInfo,
    launcherVersion,
  } = useSettingsState()
  // null is the overview grid; a section id is that module's own controls.
  const [section, setSection] = useState<SectionId | null>(null)
  const [search, setSearch] = useState("")

  const closeSettings = useUiStore((s) => s.closeSettings)

  // Deep-link from elsewhere in the app (currently the update banner's "view
  // progress" / "update now"), which needs to open a specific module rather
  // than the overview. Keyed off the signal too, so re-requesting a section the
  // user has since navigated away from still re-opens it.
  const { deepLinkSection, deepLinkSignal } = useUiStore(
    useShallow((s) => ({
      deepLinkSection: s.settingsSection,
      deepLinkSignal: s.settingsSectionSignal,
    })),
  )
  useEffect(() => {
    if (!deepLinkSection) return
    if (SECTIONS.some((s) => s.id === deepLinkSection)) {
      setSection(deepLinkSection as SectionId)
    }
  }, [deepLinkSection, deepLinkSignal])

  // Publish what is on screen so the rest of the app can defer to it — the
  // update banner hides while Updates is open. Cleared on the way out, which
  // covers both closing the module and leaving Settings altogether.
  const setVisibleSection = useUiStore((s) => s.setVisibleSettingsSection)
  useEffect(() => {
    setVisibleSection(section)
    return () => setVisibleSection(null)
  }, [section, setVisibleSection])

  const {
    updater,
    check: checkUpdate,
    download: downloadUpdate,
    install: installUpdate,
  } = useLauncherUpdater(section === "about", showToast)
  const {
    clearingCache,
    clearCache,
    localResetOpen,
    openLocalReset,
    closeLocalReset,
    performLocalReset,
  } = useSettingsIO(showToast)

  // About reads the host snapshot; polling stays scoped to it.
  const systemInfo = useSystemInfo(section === "about")

  const summaries = useSectionSummaries({
    values,
    launcherVersion,
  })

  return (
    <section className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="text-sm font-semibold">{t("nav.settings")}</span>
        <button
          type="button"
          aria-label={t("common.close")}
          onClick={() => (section === null ? closeSettings() : setSection(null))}
          className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      {section === null ? (
        <SettingsOverview
          summaries={summaries}
          search={search}
          onSearchChange={setSearch}
          onSelect={setSection}
          onReset={openLocalReset}
        />
      ) : (
        <>
          {/* Same rule as the overview: the header is fixed chrome, only the
              module's own controls scroll. */}
          <DetailHeader section={section} onBack={() => setSection(null)} />

          <div className="min-h-0 flex-1 overflow-y-auto px-9 pt-5 pb-6">
            {section === "general" && (
              <GeneralSection values={values} update={update} />
            )}
            {section === "appearance" && <AppearanceSection />}
            {section === "privacy" && (
              <PrivacySection
                clearingCache={clearingCache}
                clearCache={clearCache}
                openLocalReset={openLocalReset}
              />
            )}
            {section === "about" && (
              <AboutSection
                values={values}
                update={update}
                launcherVersion={launcherVersion}
                runtimeInfo={runtimeInfo}
                systemInfo={systemInfo}
                updater={updater}
                checkUpdate={checkUpdate}
                downloadUpdate={downloadUpdate}
                installUpdate={installUpdate}
              />
            )}

            <RelatedSettings
              ids={RELATED[section] ?? []}
              onSelect={setSection}
            />
          </div>
        </>
      )}

      <ConfirmDialog
        open={localResetOpen}
        title={t("settings.localResetDialog.title")}
        description={t("settings.localResetDialog.description")}
        confirmLabel={t("settings.localResetDialog.confirm")}
        destructive
        onCancel={closeLocalReset}
        onConfirm={performLocalReset}
      >
        <ResetSummary
          prefix="settings.localResetDialog"
          affected={LOCAL_RESET_AFFECTED}
          kept={LOCAL_RESET_KEPT}
        />
      </ConfirmDialog>
    </section>
  )
}
