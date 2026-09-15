import { resolve } from 'node:path';

import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * The desktop build of the workspace.
 *
 * A SECOND build target over the same source — `next build` is untouched and
 * still produces the web app. This one bundles the same pages into static
 * files the launcher ships inside its installer and loads from disk, so the
 * desktop app opens instantly, works offline, and loads nothing from a remote
 * origin.
 *
 * What makes that possible without forking the code is the alias table below:
 * the handful of `next/*` modules the pages import resolve to small local
 * equivalents (see desktop/shims). No page, component or lib file changes.
 */
export default defineConfig(({ mode }) => {
  // Supabase project config, baked in at build time (unlike NEXT_PUBLIC_API_URL,
  // this isn't meant to be swapped per self-hosted deployment at runtime).
  // Loaded from `workspace/.env` — the same file docker-compose reads for the
  // web build — so there's one source of truth for both. `''` (3rd arg) loads
  // every var regardless of a VITE_/NEXT_PUBLIC_ prefix; loadEnv only reads
  // process.env otherwise, it doesn't mutate it.
  const env = loadEnv(mode, resolve(__dirname, '..'), '');
  const supabaseUrl = env.SUPABASE_URL || process.env.SUPABASE_URL || '';
  const supabaseAnonKey = env.SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';
  if (!supabaseUrl || !supabaseAnonKey) {
    // A silent empty value here means the whole desktop app dies on
    // "process is not defined"-adjacent auth failures with no clue why —
    // fail loud at build time instead.
    console.warn(
      '[vite.config] SUPABASE_URL/SUPABASE_ANON_KEY are not set (checked workspace/.env) — ' +
      'the desktop build will ship with no Supabase config and sign-in will not work.',
    );
  }

  return {
    root: resolve(__dirname, 'desktop'),
    // The web app's own static files (logos, icons, the notification sound). Vite
    // would otherwise look for them beside the entry, in desktop/, and the pages
    // reference them by absolute paths such as `/pai-emblem.png`, which is why the
    // launcher serves this bundle over a custom scheme rather than from file:.
    publicDir: resolve(__dirname, 'public'),
    // Relative, because the pages are loaded from a file: URL rather than served
    // from the root of a host.
    base: './',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: [
        { find: /^next\/navigation$/, replacement: resolve(__dirname, 'desktop/shims/next-navigation.ts') },
        { find: /^next\/image$/, replacement: resolve(__dirname, 'desktop/shims/next-image.tsx') },
        { find: /^next\/link$/, replacement: resolve(__dirname, 'desktop/shims/next-link.tsx') },
        { find: /^next\/script$/, replacement: resolve(__dirname, 'desktop/shims/next-script.tsx') },
        { find: /^next\/dynamic$/, replacement: resolve(__dirname, 'desktop/shims/next-dynamic.ts') },
        { find: /^next\/font\/google$/, replacement: resolve(__dirname, 'desktop/shims/next-font-google.ts') },
        // `@/` is the same project-root alias the web build uses (tsconfig paths).
        { find: /^@\//, replacement: `${resolve(__dirname)}/` },
      ],
    },
    // `process.env.NEXT_PUBLIC_*` is a Next.js build-time substitution; Vite
    // leaves it alone and the page dies on `process is not defined`. These put
    // the same values back — with the API base left resolvable at RUN time, so a
    // self-hosted deployment can be pointed at without a rebuild: the launcher's
    // preload sets `globalThis.__OA_API_URL__` before the page's first script.
    define: {
      // A bare identifier, not an expression: esbuild's `define` takes only
      // entity names and literals. index.html gives it a default, and the
      // launcher's preload — which runs before any page script — can set it to
      // a self-hosted endpoint first.
      'process.env.NEXT_PUBLIC_API_URL': '__OA_API_URL__',
      // Supabase project config — baked in as literal strings at build time
      // (from workspace/.env), unlike API_URL above. See the loadEnv() call up top.
      'process.env.NEXT_PUBLIC_SUPABASE_URL': JSON.stringify(supabaseUrl),
      'process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY': JSON.stringify(supabaseAnonKey),
      // Analytics is deliberately off in the desktop build; see shims/next-script.
      'process.env.NEXT_PUBLIC_POSTHOG_KEY': 'undefined',
      'process.env.NEXT_PUBLIC_POSTHOG_HOST': 'undefined',
      'process.env.NEXT_PUBLIC_GA_ID': 'undefined',
      // `--mode development` keeps React's development build, whose errors are
      // sentences rather than "Minified React error #NNN" — the difference
      // between an afternoon and a minute when the app only breaks once bundled.
      'process.env.NODE_ENV': JSON.stringify(
        mode === 'development' ? 'development' : 'production',
      ),
    },
    build: {
      outDir: resolve(__dirname, 'dist-desktop'),
      emptyOutDir: true,
      // The launcher ships this; a source map would double the installer for a
      // stack trace nobody can act on in a signed build.
      sourcemap: false,
      // Readable names in a development build, for the same reason.
      minify: mode !== 'development',
    },
  };
});
