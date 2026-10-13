import type { NextConfig } from "next";

/**
 * WS-55 EA-4a (`external_agent_access.md` §10.5): the consent page of an
 * outside agent may never sit in a frame. A page that approves an agent with
 * one click is a clickjacking target, so no other site may frame it. CSP
 * `frame-ancestors` is the rule of modern browsers, and `X-Frame-Options` is
 * the same rule for older ones. `/connect/:path*` also matches `/connect`.
 * Fence: `e2e/connect-authorize.spec.ts` reads both headers off the page.
 */
const CONNECT_FRAME_HEADERS = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/connect/:path*", headers: CONNECT_FRAME_HEADERS }];
  },
  // 🔴 **The deploy builds BESIDE the running build, never over it.**
  //
  // `scripts/vps_apply.sh` sets `NEXT_DIST_DIR=.next.staging`, builds into it,
  // and renames it onto `.next` only after the build succeeds. Without this
  // override the only way to get a clean build was `rm -rf .next` FIRST, which
  // deletes the directory the running server is serving from.
  //
  // Measured 2026-09-01: `app.metorite.com` answered HTTP 500 on every route
  // while `acb-workbench` restart-looped every 5s — "Could not find a
  // production build in the '.next' directory" — for the whole build. A build
  // that FAILED left it that way permanently.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // esbuild (used by /api/artifacts/compile to bundle React artifacts) ships a
  // native binary. Tracing it into the server bundle fails the build outright —
  // Next tries to parse the executable as source and dies on its non-UTF8 bytes.
  // Keeping it external makes the route `require` it at runtime instead.
  serverExternalPackages: ["esbuild"],
  images: {
    remotePatterns: [
      // Clearbit logo API — high-quality company logos
      { protocol: "https", hostname: "logo.clearbit.com" },
      // Google favicon service — universal fallback, works for every domain
      { protocol: "https", hostname: "www.google.com" },
      // DuckDuckGo favicon service — additional fallback
      { protocol: "https", hostname: "icons.duckduckgo.com" },
    ],
  },
};

export default nextConfig;
