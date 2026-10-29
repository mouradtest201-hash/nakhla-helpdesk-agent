import type { NextConfig } from "next";

const agentUrl = (process.env.AGENT_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  turbopack: {
    root: process.cwd(),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${agentUrl}/api/:path*` },
      { source: "/health", destination: `${agentUrl}/health` },
    ];
  },
};

export default nextConfig;
