'use client';

/**
 * Retired route.
 *
 * The student's profile is a first-class workspace view now — account name and
 * photo sit at the top of it, alongside their canonical academic record — so
 * this page is no longer a settings screen. It stays mounted purely as a
 * redirect, because bookmarks and old links to /settings/profile should land
 * on the real page rather than a 404.
 *
 * `?view=profile` is read once by the workspace shell (see
 * components/layout/layout-context.tsx) and then dropped from the URL.
 */

import { use, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useT } from '@/lib/i18n';

export default function RetiredProfileSettingsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const t = useT();

  useEffect(() => {
    // Carry a self-hosted ?token= through, or the workspace gate would ask for
    // credentials the visitor already had.
    const token = searchParams.get('token');
    const query = new URLSearchParams({ view: 'profile' });
    if (token) query.set('token', token);
    router.replace(`/${workspaceId}?${query}`);
  }, [router, workspaceId, searchParams]);

  return (
    <div className="flex items-center justify-center py-16">
      <p className="animate-pulse text-sm text-muted-foreground">{t('admin.loading')}</p>
    </div>
  );
}
