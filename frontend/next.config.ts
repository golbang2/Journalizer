import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  // A second dev server (e.g. pointed at a test backend) must not share build output with the main one.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The browser only talks to this origin; /api/* is forwarded to FastAPI so the session cookie
  // is first-party and no CORS setup is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  }
};

export default nextConfig;
