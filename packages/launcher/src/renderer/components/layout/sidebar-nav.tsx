import React from "react"
import { useShallow } from "zustand/react/shallow"
import { useTranslation } from "react-i18next"

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@renderer/components/ui/sidebar"
import { useUiStore } from "@renderer/store/ui"
import { capture } from "@renderer/lib/analytics"
import { NAV_ITEMS, NAV_SECTIONS } from "./nav-config"

export function SidebarNav(): React.JSX.Element {
  const { t } = useTranslation()
  const { currentTab, setCurrentTab } = useUiStore(
    useShallow((s) => ({
      currentTab: s.currentTab,
      setCurrentTab: s.setCurrentTab,
    })),
  )

  const open = (id: string): void => {
    capture("tab_switched", { tab: id })
    setCurrentTab(id)
  }

  return (
    <>
      {NAV_SECTIONS.map((section) => (
        <SidebarGroup key={section}>
          <SidebarGroupLabel className="text-3xs font-semibold tracking-wider text-sidebar-muted uppercase">
            {t(`nav.sections.${section}`)}
          </SidebarGroupLabel>
          <SidebarGroupContent>
            {/* Centred once collapsed. The rows are forced to a square there,
                and a stretch column pins that square to the left edge — fine
                while the rail was exactly one square wide, visibly off-centre
                now that macOS widens it to clear the traffic lights. */}
            <SidebarMenu className="group-data-[collapsible=icon]:items-center">
              {NAV_ITEMS.filter((i) => i.section === section).map((item) => (
                <SidebarMenuItem key={item.id}>
                  <SidebarMenuButton
                    isActive={currentTab === item.id}
                    onClick={() => open(item.id)}
                    data-tour={item.id}
                    data-testid={`nav-${item.id}`}
                    // Both, deliberately: `tooltip` only renders while the rail
                    // is collapsed and carries the label the icon replaced, so
                    // the native `title` keeps the longer description available
                    // while the rail is expanded.
                    title={t(`nav.items.${item.id}.description`)}
                    tooltip={t(`nav.items.${item.id}.label`)}
                  >
                    <item.icon />
                    <span>{t(`nav.items.${item.id}.label`)}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </>
  )
}
