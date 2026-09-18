import React from "react"
import { useTranslation } from "react-i18next"
import { Eraser, ExternalLink, Palette } from "lucide-react"
import { PRODUCT_LINKS } from "../../../../shared/product-links"
import { Button } from "@renderer/components/ui/button"
import { Spinner } from "@renderer/components/ui/spinner"
import { SettingsCard, Row } from "../components/settings-card"

interface Props { clearingCache: boolean; clearCache: () => void | Promise<void>; openLocalReset: () => void }

export function PrivacySection({ clearingCache, clearCache, openLocalReset }: Props): React.JSX.Element {
  const { t } = useTranslation()
  return <>
    <SettingsCard title={t("settings.privacy.policyGroup")}>
      <Row label={t("settings.privacy.policy")} desc={t("settings.privacy.policyDesc")}>
        <Button size="sm" variant="outline" onClick={() => window.api.openExternal(PRODUCT_LINKS.privacy)}>
          {t("settings.privacy.viewPolicy")} <ExternalLink />
        </Button>
      </Row>
    </SettingsCard>
    <SettingsCard title={t("settings.privacy.localGroup")} desc={t("settings.privacy.localGroupDesc")}>
      <Row label={t("settings.privacy.clearCache")} desc={t("settings.privacy.clearCacheDesc")}>
        <Button size="sm" variant="destructive-ghost" disabled={clearingCache} onClick={() => void clearCache()}>
          {clearingCache ? <Spinner /> : <Eraser />} {t("common.clear")}
        </Button>
      </Row>
      <Row label={t("settings.privacy.resetLocal")} desc={t("settings.privacy.resetLocalDesc")}>
        <Button size="sm" variant="destructive-ghost" onClick={openLocalReset}>
          <Palette /> {t("common.reset")}
        </Button>
      </Row>
    </SettingsCard>
  </>
}
