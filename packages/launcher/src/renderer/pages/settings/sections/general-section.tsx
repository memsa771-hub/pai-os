import React from "react"
import { useTranslation } from "react-i18next"
import { Switch } from "@renderer/components/ui/switch"
import { SettingsCard, Row } from "../components/settings-card"
import type { SettingsValues, Update } from "../use-settings-state"

interface Props { values: SettingsValues; update: Update }

export function GeneralSection({ values, update }: Props): React.JSX.Element {
  const { t } = useTranslation()
  return (
    <SettingsCard title={t("settings.general.startupGroup")}>
      <Row label={t("settings.general.startOnBoot")} desc={t("settings.general.startOnBootDesc")}>
        <Switch checked={values.startOnBoot} onCheckedChange={(value) => update("startOnBoot", value)} />
      </Row>
      <Row label={t("settings.general.minimizeToTray")} desc={t("settings.general.minimizeToTrayDesc")}>
        <Switch checked={values.minimizeToTray} onCheckedChange={(value) => update("minimizeToTray", value)} />
      </Row>
    </SettingsCard>
  )
}
