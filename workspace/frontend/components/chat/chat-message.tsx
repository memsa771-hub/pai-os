'use client';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Copy, Check, User, FileIcon, Download, Eye, Cog, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import {
  isDocumentAttachment,
  SETTLED_DOCUMENT_STAGES,
  type DocumentStage,
} from '@/lib/document-types';
import type { WorkspaceMessage, WorkspaceAgent } from '@/lib/types';
import { AgentAvatar } from '@/components/agents/agent-avatar';
import { MarkdownContent } from './markdown-content';
import { workspaceApi } from '@/lib/api';
import { useLayout } from '@/components/layout/layout-context';
import { useWorkspace } from '@/lib/workspace-context';
import { useFormatters, useT } from '@/lib/i18n';
import { agentLabel } from '@/lib/helpers';

interface Attachment {
  fileId: string;
  filename: string;
  contentType: string;
  url: string;
}

function humanColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return `hsl(${hash % 360} 55% 82%)`;
}

function isPreviewable(contentType: string, filename: string): boolean {
  if (contentType?.startsWith('image/')) return true;
  if (contentType === 'text/html' || /\.html?$/i.test(filename)) return true;
  if (contentType === 'text/markdown' || /\.mdx?$/i.test(filename)) return true;
  if (contentType?.startsWith('text/') || /\.(json|js|ts|tsx|jsx|py|rs|go|java|rb|sh|yaml|yml)$/i.test(filename)) return true;
  return false;
}

/** Poll every few seconds; stop once the stage settles or after ~5 minutes. */
const STAGE_POLL_MS = 3000;
const STAGE_POLL_MAX = 100;

/**
 * Live processing stage under a PDF/DOCX attachment.
 *
 * Reads the server's stage rather than local state, so a student who leaves
 * the chat and comes back mid-processing sees where the document really is —
 * previously the file just sat there looking idle for the ~30s extraction.
 */
function DocumentStatus({ fileId }: { fileId: string }) {
  const t = useT();
  const [stage, setStage] = useState<DocumentStage | null>(null);

  useEffect(() => {
    let cancelled = false;
    let polls = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await workspaceApi.getDocumentStage(fileId);
        if (cancelled) return;
        setStage(next);
        if (SETTLED_DOCUMENT_STAGES.includes(next)) return;
      } catch {
        // Transient: keep the last known stage and try again.
      }
      polls += 1;
      if (!cancelled && polls < STAGE_POLL_MAX) timer = setTimeout(poll, STAGE_POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [fileId]);

  if (stage === null || stage === 'unsupported') return null;

  const inProgress = stage === 'reading' || stage === 'understanding';
  const label = stage === 'reading'
    ? t('chat.documentReading')
    : stage === 'understanding'
      ? t('chat.documentUnderstanding')
      : stage === 'done'
        ? t('chat.documentDone')
        : t('chat.documentFailed');

  return (
    <div
      className={cn(
        'mt-1 flex items-center gap-1.5 text-2xs',
        stage === 'failed' ? 'text-destructive' : 'text-muted-foreground',
      )}
      role="status"
      aria-live="polite"
    >
      {inProgress && <Loader2 className="size-3 animate-spin" aria-hidden="true" />}
      {stage === 'done' && <Check className="size-3 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />}
      <span>{label}</span>
    </div>
  );
}

function Attachments({ items }: { items: Attachment[] }) {
  if (!items || items.length === 0) return null;

  const { setViewMode } = useLayout();
  const { setSelectedFileId } = useWorkspace();

  const openPreview = useCallback((fileId: string) => {
    setSelectedFileId(fileId);
    setViewMode('files');
  }, [setSelectedFileId, setViewMode]);

  // Regenerate URLs from fileId to ensure they include current auth token
  const fixedItems = useMemo(() =>
    items.map((a) => ({ ...a, url: workspaceApi.getFileUrl(a.fileId) })),
    [items]
  );

  const images = fixedItems.filter((a) => a.contentType?.startsWith('image/'));
  const files = fixedItems.filter((a) => !a.contentType?.startsWith('image/'));

  return (
    <div className="mt-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((img) => (
            <button
              key={img.fileId}
              type="button"
              onClick={() => openPreview(img.fileId)}
              className="block rounded-lg overflow-hidden border hover:shadow-md transition-shadow max-w-sm cursor-pointer text-left"
            >
              <img
                src={img.url}
                alt={img.filename}
                className="max-h-64 w-auto object-contain"
                loading="lazy"
              />
            </button>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {files.map((file) => {
            const previewable = isPreviewable(file.contentType, file.filename);
            const chip = previewable ? (
              <button
                type="button"
                onClick={() => openPreview(file.fileId)}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border bg-muted hover:bg-muted/80 transition-colors text-sm cursor-pointer"
              >
                <Eye className="size-4 text-muted-foreground shrink-0" />
                <span className="truncate max-w-[200px]">{file.filename}</span>
              </button>
            ) : (
              <a
                href={file.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-2 rounded-lg border bg-muted hover:bg-muted/80 transition-colors text-sm"
              >
                <FileIcon className="size-4 text-muted-foreground shrink-0" />
                <span className="truncate max-w-[200px]">{file.filename}</span>
                <Download className="size-3 text-muted-foreground shrink-0" />
              </a>
            );
            return (
              <div key={file.fileId} className="flex flex-col">
                {chip}
                {isDocumentAttachment(file.filename, file.contentType) && (
                  <DocumentStatus fileId={file.fileId} />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * Badge for a message posted by `operator._post_result` (message_type
 * "operator_result", metadata.execution_status set — see
 * app/services/operator.py / cloud_agent._post_response). PAI Counselor is
 * still the sender the student sees below this, but the badge makes clear
 * this particular reply is a finished PAI Operator execution result, not an
 * ordinary conversational turn.
 */
function OperatorResultBadge({ status }: { status: string | undefined }) {
  const t = useT();
  const label = status === 'completed'
    ? t('paiSystem.operatorResultCompleted')
    : status === 'needs_user_action'
      ? t('paiSystem.operatorResultNeedsAction')
      : t('paiSystem.operatorResultFailed');
  const tone = status === 'completed'
    ? 'text-emerald-600 dark:text-emerald-400'
    : status === 'needs_user_action'
      ? 'text-amber-600 dark:text-amber-400'
      : 'text-destructive';

  return (
    <div className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-muted-foreground">
      <Cog className="size-3" />
      <span>{t('paiSystem.operatorName')}</span>
      <span aria-hidden="true">·</span>
      <span>{t('paiSystem.operatorRole')}</span>
      <span aria-hidden="true">·</span>
      <span className={tone}>{label}</span>
    </div>
  );
}

interface ChatMessageProps {
  message: WorkspaceMessage;
  agents?: WorkspaceAgent[];
  /** True only for the trailing message — gates the suggestion chips. */
  isLast?: boolean;
  /** Send a suggested prompt as the user's message (tap-to-ask chips). */
  onSuggestion?: (text: string) => void;
}

export const ChatMessage = memo(function ChatMessage({ message, agents = [], isLast = false, onSuggestion }: ChatMessageProps) {
  const { currentUser } = useWorkspace();
  const t = useT();
  const { formatTime } = useFormatters();
  const isHuman = message.senderType === 'human' || message.senderType === 'user';
  const isSystem = message.messageType === 'status';
  const [copied, setCopied] = useState(false);

  const agentNames = useMemo(() => agents.map((a) => a.agentName), [agents]);
  const agentLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const a of agents) {
      if (a.displayName) labels[a.agentName] = agentLabel(a);
    }
    return labels;
  }, [agents]);
  const agent = agents.find((a) => a.agentName === message.senderName);
  const rawAttachments = (message.metadata?.attachments as Record<string, unknown>[]) || [];
  const attachments: Attachment[] = rawAttachments.map((a) => ({
    fileId: (a.fileId || a.file_id || '') as string,
    filename: (a.filename || '') as string,
    contentType: (a.contentType || a.content_type || '') as string,
    url: '',
  }));

  const timestamp = message.createdAt ? formatTime(message.createdAt) : null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t('chat.copyFailed'));
    }
  };

  // Status messages — subtle inline
  if (isSystem) {
    const isQueued = message.content.includes('queued');
    return (
      <div className="flex justify-center py-1">
        <span className={cn(
          'text-xs italic',
          isQueued
            ? 'text-foreground/80'
            : 'text-muted-foreground'
        )}>
          {agent ? agentLabel(agent) : message.senderName}: {message.content}
        </span>
      </div>
    );
  }

  // ── Human message ──
  // The workspace has exactly one human (its owner), so this is always "your"
  // turn — right-aligned bubble, like an ordinary chat app, with the agent's
  // reply below it on the left.
  if (isHuman) {
    const isCurrentUser = !!message.senderId && message.senderId === currentUser.id;
    const seed = message.senderId || message.senderName || 'human';
    const displayName = isCurrentUser
      ? 'You'
      : (message.senderName && message.senderName !== 'user' ? message.senderName : 'User');

    return (
      <div className="py-2">
        <div className="flex items-start justify-end gap-3">
          <div className="flex min-w-0 max-w-[75%] flex-col items-end">
            <div className="flex items-baseline gap-2">
              {timestamp && (
                <span className="text-[11px] text-muted-foreground">{timestamp}</span>
              )}
              <span className="text-sm font-semibold text-foreground">{displayName}</span>
            </div>
            <div className="mt-0.5 rounded-2xl rounded-tr-sm bg-primary/10 px-3.5 py-2 text-left text-sm leading-relaxed">
              <MarkdownContent content={message.content} agentNames={agentNames} agentLabels={agentLabels} />
              <Attachments items={attachments} />
            </div>
          </div>
          <div
            className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full"
            style={{ backgroundColor: humanColor(seed) }}
          >
            <User className="size-3.5 text-zinc-700" />
          </div>
        </div>
      </div>
    );
  }

  // ── Agent message ──
  // Full-bleed, no bubble — ChatGPT's assistant turn. The avatar and name stay:
  // a thread can have several agents, so the author still has to be readable.
  return (
    <div className="group py-2">
      <div className="flex items-start gap-3">
        <AgentAvatar name={message.senderName} size={28} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-semibold text-foreground">
              {agent ? agentLabel(agent) : message.senderName}
            </span>
            {timestamp && (
              <span className="text-[11px] text-muted-foreground">{timestamp}</span>
            )}
          </div>
          <div className="mt-0.5 text-sm leading-relaxed">
            {message.messageType === 'operator_result' && (
              <OperatorResultBadge status={message.metadata?.execution_status as string | undefined} />
            )}
            <MarkdownContent content={message.content} agentNames={agentNames} agentLabels={agentLabels} />
            <Attachments items={attachments} />

            {/* Tap-to-ask chips (e.g. PAI Counselor's seeded welcome). Only on the
                trailing message: once the user replies, the moment is over. */}
            {isLast && onSuggestion && Array.isArray(message.metadata?.suggestions) && (
              <div className="mt-2.5 flex flex-wrap gap-2">
                {(message.metadata.suggestions as string[]).slice(0, 4).map((s) => (
                  <button
                    key={s}
                    onClick={() => onSuggestion(s)}
                    className="rounded-full border border-primary/30 bg-primary/[0.04] px-3.5 py-2 text-[13px] font-medium text-primary hover:bg-primary/10 active:scale-[0.98] transition"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}

            {/* Action bar — revealed on hover, as in ChatGPT */}
            <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-1.5 text-xs text-muted-foreground hover:text-foreground"
                onClick={handleCopy}
                aria-label={t('chat.copyMessage')}
              >
                {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
                {copied ? t('common.copied') : t('common.copy')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});
