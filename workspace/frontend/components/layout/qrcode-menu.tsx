'use client';

import { useCallback, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { QrcodeIcon } from '@/components/icons/qrcode-icon';
import { useT } from '@/lib/i18n';

/**
 * Where a scannable link has to point. Always the canonical public app URL —
 * never `window.location.origin`, which can be an internal/legacy host (e.g.
 * the desktop app's embedded workspace view, or localhost) rather than the
 * one address a phone can actually open.
 */
const HOSTED_ORIGIN = 'https://placement-ai.com';

function shareOrigin(): string {
  return HOSTED_ORIGIN;
}

interface QrcodeMenuProps {
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
}

/**
 * Rail action that shows a QR code for opening Placement AI on another
 * device — a DEVICE HANDOFF, not a share link. The code points at the app
 * root with no workspace id and no token: scanning it just opens
 * `shareOrigin()`, and whichever device that is resolves the *same signed-in
 * Supabase account* to the *same* `owner_user_id`-owned workspace (see
 * `app/page.tsx`'s `ResolvingWorkspace` and the backend's
 * `GET /v1/account/workspace`). If that device isn't signed in yet, it lands
 * on the sign-in gate first and resolves the same way right after.
 *
 * Deliberately carries no secret: no workspace token, no membership grant,
 * no way for a DIFFERENT person scanning it to see anything but their own
 * (or nobody's) workspace. A student's workspace is private — this only ever
 * gets THEM into THEIR workspace from a second device.
 */
export function QrcodeMenu({ side = 'right', align = 'end' }: QrcodeMenuProps = {}) {
  const t = useT();
  const [open, setOpen] = useState(false);

  const shareUrl = `${shareOrigin()}/`;

  const handleCopy = useCallback(async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        // Fallback for in-app browsers / insecure contexts without the Clipboard API
        const ta = document.createElement('textarea');
        ta.value = shareUrl;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast.success(t('qrcode.copied'));
    } catch {
      toast.error(t('qrcode.copyFailed'));
    }
  }, [shareUrl, t]);

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label={t('qrcode.trigger')}
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          >
            <QrcodeIcon />
          </button>
        </TooltipTrigger>
        <TooltipContent side={side} align={align}>
          {t('qrcode.trigger')}
        </TooltipContent>
      </Tooltip>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('qrcode.dialogTitle')}</DialogTitle>
            <DialogDescription>{t('qrcode.dialogDescription')}</DialogDescription>
          </DialogHeader>

          <div className="flex flex-col items-center gap-3 pb-2">
            {/* The code keeps a white quiet zone in both themes — inverting it
                for dark mode is what breaks scanners. */}
            <button
              type="button"
              onClick={handleCopy}
              title={t('qrcode.clickToCopy')}
              className="rounded-lg bg-white p-4 ring-1 ring-border transition-transform hover:scale-[1.02] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {/* `marginSize` is the spec's 4-module quiet zone, in modules —
                  dropping it leaves scanners with nothing to lock onto once
                  the dialog behind it is dark. */}
              <QRCodeSVG value={shareUrl} size={200} level="M" marginSize={4} />
            </button>
            <p className="text-muted-foreground text-xs">{t('qrcode.clickToCopy')}</p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
