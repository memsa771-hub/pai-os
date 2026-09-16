import { Settings } from "lucide-react"
import { useTranslation } from "react-i18next"
import { LauncherUpdateBanner } from "@renderer/components/LauncherUpdateBanner"
import { useUiStore } from "@renderer/store/ui"

/**
 * Native window chrome. PAI Desktop is one screen — the account's Workspace —
 * so this bar carries only the traffic-lights/window-controls clearance, the
 * update banner, and a small gear into Settings. Workspace owns all product
 * navigation inside its own web UI.
 */
export function TitleBar(): React.JSX.Element {
  const { t } = useTranslation()
  const openSettings = useUiStore((s) => s.openSettings)
  return (
    <header className="titlebar-drag relative z-20 flex h-(--mode-bar-h) shrink-0 items-center gap-3 border-b bg-sidebar pr-(--window-controls-w) pl-(--traffic-lights-w)">
      <div className="titlebar-no-drag ml-2 flex min-w-0 items-center gap-2">
        <button
          type="button"
          aria-label={t("nav.settings")}
          title={t("nav.settings")}
          onClick={openSettings}
          className="flex size-6 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Settings className="size-3.5" />
        </button>
      </div>
      <div className="titlebar-no-drag mx-auto flex min-w-0 justify-center">
        <LauncherUpdateBanner inline />
      </div>
    </header>
  )
}
