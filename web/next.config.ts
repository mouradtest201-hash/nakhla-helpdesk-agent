import type { NextConfig } from "next";

// The browser only talks to this app; /api and /health are proxied to the agent (no CORS needed).
const AGENT_URL = (process.env.AGENT_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${AGENT_URL}/api/:path*` },
      { source: "/health", destination: `${AGENT_URL}/health` },
    ];
  },
};

export default nextConfig;
