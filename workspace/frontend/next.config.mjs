/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // No rewrites. There was a `/wsapi/:path*` proxy to the retired OpenAgents
  // endpoint; nothing in the app ever called it, and a second undocumented
  // route to the API is worth less than the confusion it causes. The client
  // talks to NEXT_PUBLIC_API_URL directly — see lib/config.ts.
  env: {
    // Surfaces held back for a later release. Spelled out here (rather than
    // left to an unset NEXT_PUBLIC_* falling through to webpack's process
    // shim) so the flag is inlined as a literal and the hidden UI drops out
    // of the bundle — the same thing vite.config.ts does for desktop.
    NEXT_PUBLIC_ENABLE_DMS: process.env.NEXT_PUBLIC_ENABLE_DMS || 'false',
    NEXT_PUBLIC_ENABLE_INBOX: process.env.NEXT_PUBLIC_ENABLE_INBOX || 'false',
    NEXT_PUBLIC_ENABLE_TASKS: process.env.NEXT_PUBLIC_ENABLE_TASKS || 'false',
    NEXT_PUBLIC_ENABLE_WORKFLOWS: process.env.NEXT_PUBLIC_ENABLE_WORKFLOWS || 'false',
  },
};

export default nextConfig;
