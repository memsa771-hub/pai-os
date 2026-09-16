import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin, loadEnv } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Supabase project config for the main process's own Supabase REST client
// (src/main/auth/supabase.ts) — baked in at build time, read from
// workspace/.env so it can't drift from what the workspace backend/frontend
// use (same project, one source of truth). `''` mode + empty prefix loads
// every var regardless of a VITE_/NEXT_PUBLIC_ prefix.
const workspaceEnv = loadEnv('', resolve(__dirname, '../../workspace'), '')
const SUPABASE_URL = workspaceEnv.SUPABASE_URL || process.env.SUPABASE_URL || ''
const SUPABASE_ANON_KEY = workspaceEnv.SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || ''
if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // A silently empty value here means sign-in fails with no clue why — fail
  // loud at build time instead.
  console.warn(
    '[electron.vite.config] SUPABASE_URL/SUPABASE_ANON_KEY are not set (checked workspace/.env) — ' +
    'PAI Desktop will ship with no Supabase config and native sign-in will not work.'
  )
}

// The same backend/web origins Web itself uses (src/main/auth/endpoints.ts's
// defaults), so a change to workspace/.env moves both without a source edit.
// Left unset here, endpoints.ts's own hardcoded defaults apply — never
// invented in this file.
const PAI_API_BASE = workspaceEnv.NEXT_PUBLIC_API_URL || process.env.PAI_API_BASE || ''
const PAI_WEB_BASE = workspaceEnv.NEXT_PUBLIC_APP_URL || process.env.PAI_WEB_BASE || ''

export default defineConfig({
  main: {
    define: {
      'process.env.SUPABASE_URL': JSON.stringify(SUPABASE_URL),
      'process.env.SUPABASE_ANON_KEY': JSON.stringify(SUPABASE_ANON_KEY),
      'process.env.PAI_API_BASE': JSON.stringify(PAI_API_BASE),
      'process.env.PAI_WEB_BASE': JSON.stringify(PAI_WEB_BASE)
    },
    plugins: [externalizeDepsPlugin()]
  },
  preload: {
    build: {
      rollupOptions: {
        input: {
          index: resolve('src/preload/index.ts'),
          // Second entry, loaded only by the embedded workspace view: it runs
          // on the workspace's own https origin and must carry nothing but the
          // session handoff. See src/main/workspace-host.ts.
          'workspace-view': resolve('src/preload/workspace-view.ts')
        }
      }
    },
    plugins: [externalizeDepsPlugin()]
  },
  renderer: {
    resolve: {
      dedupe: ['react', 'react-dom', 'sonner', 'radix-ui'],
      alias: {
        '@renderer': resolve('src/renderer')
      }
    },
    plugins: [react(), tailwindcss()]
  }
})
