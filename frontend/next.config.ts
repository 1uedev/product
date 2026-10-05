import type { NextConfig } from "next";

// Production: the Caddy gateway serves /api and /auth on the same origin. For local `next dev` set DEV_API_ORIGIN.
const devApi = process.env.DEV_API_ORIGIN;

const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return devApi ? [{ source: "/api/:path*", destination: `${devApi}/api/:path*` }] : [];
  },
};

export default config;
