import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  // A second dev server (e.g. pointed at a test backend) must not share build output with the main one.
  // `next build` (e.g. from test.cmd) must also never overwrite a running `next dev`, so dev keeps its own folder.
  distDir: process.env.NEXT_DIST_DIR || (process.env.NODE_ENV === "development" ? ".next-dev" : ".next"),
  experimental: {
    // AI judgment of a whole statement can take a minute; the proxy default (30s) would cut the reply off.
    proxyTimeout: 10 * 60 * 1000,
    // The proxy cuts bodies at 10MB by default, which stalls 10–15MB receipts. Allow a little more than
    // the backend's own cap (MAX_REQUEST_MB=40) so FastAPI is the one that answers 413.
    middlewareClientMaxBodySize: "41mb",
  },
  // The browser only talks to this origin; /api/* is forwarded to FastAPI so the session cookie
  // is first-party and no CORS setup is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  }
};

export default nextConfig;
