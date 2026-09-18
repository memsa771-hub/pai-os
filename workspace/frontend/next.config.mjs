/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async redirects() {
    return [
      // NOTE: `/` used to redirect to a marketing site. It is the app's own
      // entry point now — signed out it leads to /sign-in, signed in it
      // resolves the student's workspace — so that redirect stays removed.
      {
        source: '/install.sh',
        destination: 'https://raw.githubusercontent.com/openagents-org/openagents/develop/scripts/install.sh',
        permanent: false,
      },
      {
        source: '/install.ps1',
        destination: 'https://raw.githubusercontent.com/openagents-org/openagents/develop/scripts/install.ps1',
        permanent: false,
      },
    ];
  },
  // No rewrites. There was a `/wsapi/:path*` proxy to the retired OpenAgents
  // endpoint; nothing in the app ever called it, and a second undocumented
  // route to the API is worth less than the confusion it causes. The client
  // talks to NEXT_PUBLIC_API_URL directly — see lib/config.ts.
};

export default nextConfig;
