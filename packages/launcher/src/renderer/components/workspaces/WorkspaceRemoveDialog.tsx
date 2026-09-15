import React, { useEffect, useState } from "react"
import { AlertTriangle } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Checkbox } from "../ui/checkbox"
import { ConfirmDialog } from "../ui-kit"
import type { Workspace } from "../../types"

/** What removing does to THIS device — true whether or not the box is checked. */
const EFFECT_KEYS = [
  "workspaces.remove.effects.agents",
  "workspaces.remove.effects.local",
]

/**
 * Remove a workspace — from this device, and optionally from existence.
 *
 * The checkbox is the far larger act: `DELETE /v1/workspaces/{id}` takes the
 * workspace away from **everyone**, recoverable only by an admin flipping the
 * row's status back, which no UI offers — the bullet list spells out what
 * unchecked still does, because "remove" reads reversible and this is not.
 */
export function WorkspaceRemoveDialog({
  workspace,
  displayName,
  busy,
  onConfirm,
  onCancel,
}: {
  /** Non-null opens the dialog — the workspace about to be removed. */
  workspace: Workspace | null
  displayName: string
  busy: boolean
  onConfirm: (deleteRemote: boolean) => void
  onCancel: () => void
}): React.JSX.Element {
  const { t } = useTranslation()
  const [deleteRemote, setDeleteRemote] = useState(false)
  const [shown, setShown] = useState({ name: displayName })

  // Destroying the workspace for everyone must be chosen every time, never
  // inherited from the last time this dialog was open.
  useEffect(() => {
    if (workspace) setDeleteRemote(false)
  }, [workspace])

  // Held past the close so the fade-out shows the prompt the user answered.
  // Synced during render, not in an effect: an effect lands after the paint,
  // so opening this on a different workspace would show one frame of the
  // previous target's prompt — the same flash, at the other end.
  if (workspace && shown.name !== displayName) {
    setShown({ name: displayName })
  }

  return (
    <ConfirmDialog
      open={!!workspace}
      title={t("workspaces.remove.title", { name: shown.name })}
      description={t(
        deleteRemote
          ? "workspaces.remove.descriptionRemote"
          : "workspaces.remove.description",
      )}
      confirmLabel={t(
        deleteRemote
          ? "workspaces.remove.confirmRemote"
          : "workspaces.remove.confirm",
      )}
      // Destructive either way: unchecked still disconnects this device and
      // unbinds its agents, so nothing here may read as a tidy-up.
      destructive
      busy={busy}
      onConfirm={() => onConfirm(deleteRemote)}
      onCancel={onCancel}
    >
      <Effects keys={EFFECT_KEYS} />

      <label className="flex cursor-pointer items-start gap-2.5 rounded-md border bg-muted/40 px-3 py-2.5 transition-colors hover:bg-muted/70">
        <Checkbox
          checked={deleteRemote}
          disabled={busy}
          onCheckedChange={(v) => setDeleteRemote(v === true)}
          className="mt-0.5"
        />
        <span className="text-xs leading-snug text-muted-foreground">
          {t("workspaces.remove.alsoDelete")}
        </span>
      </label>

      {deleteRemote && (
        <div className="flex items-start gap-2.5 rounded-md border border-(--danger-border) bg-(--danger-bg) px-3 py-2.5">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-(--danger-text)" />
          <span className="text-xs leading-snug text-(--danger-text)">
            {t("workspaces.remove.alsoDeleteWarning")}
          </span>
        </div>
      )}
    </ConfirmDialog>
  )
}

function Effects({ keys }: { keys: string[] }): React.JSX.Element {
  const { t } = useTranslation()
  return (
    <ul className="space-y-1.5 rounded-md border bg-muted/40 px-3 py-2.5 text-xs leading-snug text-muted-foreground">
      {keys.map((key) => (
        <li key={key} className="flex items-start gap-2">
          <span className="mt-1.5 size-1 shrink-0 rounded-full bg-current" />
          <span>{t(key)}</span>
        </li>
      ))}
    </ul>
  )
}
