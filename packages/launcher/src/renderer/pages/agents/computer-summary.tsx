import React from "react"
import { useTranslation } from "react-i18next"
import { Monitor, Settings, Download } from "lucide-react"
import { Button } from "@renderer/components/ui/button"
import { useAgentsStore } from "@renderer/store/agents"
import { useUiStore } from "@renderer/store/ui"

export function ComputerSummary(): React.JSX.Element {
  const { t } = useTranslation()
  const agents = useAgentsStore((state) => state.agents)
  const navigate = useUiStore((state) => state.setCurrentTab)

  return (
    <div className="mb-7 space-y-5 rounded-2xl border bg-card p-5">
      <div className="flex items-start gap-4">
        <div className="rounded-xl bg-muted p-3">
          <Monitor className="size-6" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{t("agents.shared.thisComputer")}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {agents.length
              ? t("agents.shared.connectedCount", { count: agents.length })
              : t("agents.shared.localDescription")}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => navigate("settings")}>
          <Settings className="size-4" />
          {t("nav.items.settings.label")}
        </Button>
      </div>
      <div className="flex items-center justify-between gap-3 border-t pt-3">
        <p className="text-xs text-muted-foreground">
          {t("agents.shared.deviceSettingsHint")}
        </p>
        <Button variant="ghost" size="sm" onClick={() => navigate("install")}>
          <Download className="size-4" />
          {t("agents.shared.softwareUpdates")}
        </Button>
      </div>
    </div>
  )
}
