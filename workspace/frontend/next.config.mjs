/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // No rewrites. There was a `/wsapi/:path*` proxy to the retired OpenAgents
  // endpoint; nothing in the app ever called it, and a second undocumented
  // route to the API is worth less than the confusion it causes. The client
  // talks to NEXT_PUBLIC_API_URL directly — see lib/config.ts.
};

export default nextConfig;
