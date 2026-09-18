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
// Left unset here, endpoints.ts's own defaults apply (app/api.placement-ai.com)
// — never invented in this file.
//
// DEVELOPMENT defaults to the local stack instead. `npm run dev` against
// production is not a useful test and is an easy way to write test data into
// the real database by accident; docker-compose already serves the backend on
// :8000 and the web app on :3000. An explicit PAI_API_BASE/PAI_WEB_BASE, or a
// value in workspace/.env, still wins over both.
const DEV_API_BASE = 'http://localhost:8000'
const DEV_WEB_BASE = 'http://localhost:3000'

// `command` comes from electron-vite, and is the only reliable signal here:
// `electron-vite build` sets NODE_ENV=production itself before this file is
// loaded, so reading NODE_ENV cannot tell a dev run from a release build.
//   serve -> `npm run dev`
//   build -> `npm run build` / the installer
function origins(command: 'build' | 'serve') {
  const isDev = command === 'serve'
  const api = workspaceEnv.NEXT_PUBLIC_API_URL || process.env.PAI_API_BASE || (isDev ? DEV_API_BASE : '')
  const web = workspaceEnv.NEXT_PUBLIC_APP_URL || process.env.PAI_WEB_BASE || (isDev ? DEV_WEB_BASE : '')
  console.log(`[electron.vite.config] ${command} origins — api=${api || '(endpoints.ts default)'} web=${web || '(endpoints.ts default)'}`)
  return { api, web }
}

export default defineConfig(({ command }) => {
  const { api: PAI_API_BASE, web: PAI_WEB_BASE } = origins(command)
  return {
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
  }
})
