import React, { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { FolderOpen } from "lucide-react"

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@renderer/components/ui/dialog"
import { Button } from "@renderer/components/ui/button"
import {
  Field,
  FieldDescription,
  FieldLabel,
} from "@renderer/components/ui/field"
import { Input } from "@renderer/components/ui/input"
import type { Agent } from "@renderer/types"
import type { ToastType } from "@renderer/hooks/useToast"

interface Props {
  open: boolean
  /** The agent being reconfigured; undefined/null means "create a new one". */
  agent?: Agent | null
  onClose: () => void
  /** Called after a new agent is created, with its name. */
  onCreated: (name: string) => void
  /** Called after an existing agent's working directory is changed. */
  onChanged: () => void
  showToast: (msg: string, type?: ToastType) => void
}

/**
 * Register a new local agent, or change an existing one's working directory.
 *
 * There is no more coding-CLI catalog to pick a "type" from — every local
 * agent the daemon hosts is the same generic kind, so this only ever asks for
 * a name and a folder for it to run in.
 */
export function ManageAgentDialog({
  open,
  agent,
  onClose,
  onCreated,
  onChanged,
  showToast,
}: Props): React.JSX.Element {
  const { t } = useTranslation()
  const editing = !!agent
  const [name, setName] = useState("")
  const [folder, setFolder] = useState("")
  const [saving, setSaving] = useState(false)

  // Reseed every time the dialog opens, so a previous agent's values never
  // flash before this one's own load.
  useEffect(() => {
    if (!open) return
    setName(editing ? agent!.name : "")
    setFolder(editing ? agent!.path || "" : "")
    if (!editing) {
      window.api
        .listPaths()
        .then((p) => setFolder(p?.home || ""))
        .catch(() => {})
    }
  }, [open, editing, agent])

  const browse = async (): Promise<void> => {
    try {
      const picked = await window.api.selectDirectory(folder || undefined)
      if (picked) setFolder(picked)
    } catch (e) {
      showToast((e as Error).message, "error")
    }
  }

  const submit = async (): Promise<void> => {
    if (saving) return
    const trimmedFolder = folder.trim()
    if (!trimmedFolder) {
      showToast(t("agents.newDialog.toast.selectFolder"), "warning")
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await window.api.setAgentWorkingDir(agent!.name, trimmedFolder)
        showToast(
          t("agents.configureDialog.workdir.toast.saved", {
            path: trimmedFolder,
          }),
          "success",
        )
        onChanged()
        onClose()
        return
      }
      const trimmedName = name.trim()
      if (!/^[a-zA-Z0-9_-]+$/.test(trimmedName)) {
        showToast(t("agents.newDialog.toast.invalidName"), "warning")
        return
      }
      await window.api.addAgent({
        name: trimmedName,
        type: "openclaw",
        path: trimmedFolder,
      })
      showToast(t("agents.newDialog.toast.created", { name: trimmedName }), "success")
      onCreated(trimmedName)
      onClose()
    } catch (e) {
      showToast(
        t("agents.newDialog.toast.error", { message: (e as Error).message }),
        "error",
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent data-testid="manage-agent-dialog">
        <DialogHeader>
          <DialogTitle>
            {editing
              ? t("agents.configureDialog.title", { name: agent?.name })
              : t("agents.newDialog.title")}
          </DialogTitle>
          {!editing && (
            <DialogDescription>{t("agents.shared.folderHint")}</DialogDescription>
          )}
        </DialogHeader>

        <DialogBody className="space-y-4">
          {!editing && (
            <Field>
              <FieldLabel htmlFor="manage-agent-name">
                {t("agents.newDialog.agentName")}
              </FieldLabel>
              <Input
                id="manage-agent-name"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit()
                }}
              />
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor="manage-agent-folder">
              {editing
                ? t("agents.configureDialog.workdir.label")
                : t("agents.newDialog.workingDirectory")}
            </FieldLabel>
            <div className="flex items-center gap-2">
              <Input
                id="manage-agent-folder"
                className="flex-1"
                value={folder}
                placeholder={t("agents.newDialog.workingDirectoryPlaceholder")}
                onChange={(e) => setFolder(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit()
                }}
              />
              <Button variant="outline" onClick={() => void browse()}>
                <FolderOpen />
                {t("agents.newDialog.browse")}
              </Button>
            </div>
            {editing && (
              <FieldDescription>
                {t("agents.configureDialog.workdir.hint")}
              </FieldDescription>
            )}
          </Field>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            {t("agents.newDialog.cancel")}
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={saving || !folder.trim() || (!editing && !name.trim())}
          >
            {saving
              ? t("agents.configureDialog.workdir.saving")
              : editing
                ? t("agents.configureDialog.workdir.save")
                : t("agents.newDialog.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
