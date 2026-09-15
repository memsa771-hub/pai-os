import { resolve } from 'path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
      dedupe: ['react', 'react-dom', 'sonner', 'radix-ui'],
    alias: {
      '@renderer': resolve('src/renderer'),
        '@': resolve('../../workspace/frontend')
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/renderer/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // src/main/auth/supabase.ts reads process.env.SUPABASE_URL/ANON_KEY directly
    // (baked in at build time by electron.vite.config.ts's `define` in the real
    // app). Vitest never runs that build step, so without these the module falls
    // back to "" and every auth test breaks with "Invalid URL" — these are
    // dummy, test-only values, unrelated to any real Supabase project.
    env: {
      SUPABASE_URL: 'https://test-project.supabase.co',
      SUPABASE_ANON_KEY: 'test-anon-key'
    }
  }
})
