import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: process.env.CI ? 1 : 0,
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    // ── `localhost`, NOT `127.0.0.1`, and this is load-bearing ──────────────
    //
    // Next 16's dev server blocks `/_next/*` dev resources from an origin it
    // does not recognise, and it does NOT treat `127.0.0.1` as the same origin
    // as `localhost`. On the IP the server-rendered HTML still arrives — so the
    // page LOOKS right, shell and all — but the client bundle is refused,
    // hydration never completes, no effect runs, and no fetch is ever issued.
    // Every spec then times out waiting for data nothing requested, and the
    // page snapshot shows a permanent "Loading …".
    //
    // Measured 2026-08-21: identical run, identical stubs, hostname the only
    // variable — `127.0.0.1` renders "Loading projects…" forever, `localhost`
    // renders the tree. The server prints the reason, but it goes to the
    // webServer log where a failing run does not surface it:
    //   ⚠ Blocked cross-origin request to Next.js dev resource
    //     /_next/webpack-hmr from "127.0.0.1".
    //
    // The alternative fix is `allowedDevOrigins: ['127.0.0.1']` in
    // next.config — deliberately NOT taken: it relaxes a dev-server safety
    // default across the whole app to solve a test-only addressing problem.
    baseURL: "http://localhost:3101",
    headless: true,
    viewport: { width: 1280, height: 800 },
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Escape hatch for environments where the browser build @playwright/test
        // pins is not the one installed — a container image that ships Chromium
        // at a fixed path, for instance. Unset it and Playwright resolves its
        // own bundled browser exactly as before, so this changes nothing by
        // default; it only removes the need to patch this file by hand.
        ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
          ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } }
          : {}),
      },
    },
  ],
  webServer: {
    // ── `next dev`, not `next start`, and the reason is CP-0 ────────────────
    //
    // `next start` sets NODE_ENV=production, and `authPosture` grants its dev
    // bypass only when NODE_ENV is NOT production. So under `next start` with
    // no auth environment, the proxy answers every page with 503 (correctly —
    // that is CP-0's fail-closed posture), this readiness probe never goes
    // green, and EVERY spec in e2e/ times out before a single test runs.
    //
    // That was measured on 2026-08-14, and it had been true since `8f6eb79`
    // ("auth fails closed"). Before that commit auth failed OPEN when
    // unconfigured, so a production build with no auth env served pages and
    // the suite booted. Nothing noticed for the same reason the breakage
    // survived: nothing runs e2e/ in CI.
    //
    // Dev mode is not a bypass invented for tests. It is the affordance
    // `authPosture` already defines, deliberately keyed on NODE_ENV rather
    // than on an opt-in flag "that defaults to open", precisely so that the
    // real deployment path (`next build && next start`) cannot reach it.
    //
    // ⚠️ THE TRADE, STATED: these specs now exercise the DEV bundle. Faults
    // that appear only in a production build — minification, RSC boundary
    // differences, dead-code elimination — are not covered by this suite. The
    // `npm run build` in `test:e2e` still fails the run if the production
    // build breaks; it is simply no longer the thing the browser drives.
    // Restoring production coverage needs a real test-auth posture, which is
    // an owner decision (WS-32 spec §7), not a config tweak.
    command: "npx next dev -p 3101",
    // A page that renders for a signed-in member. Under the dev bypass that is
    // any page; the probe stays on an app route rather than a health endpoint
    // so it fails when the SHELL is broken, not merely when the process is up.
    url: "http://127.0.0.1:3101/chat",
    reuseExistingServer: false,
    // Dev compiles the route on first request, which is slower than serving a
    // prebuilt one — this is a cold Next.js compile, not a hang.
    timeout: 180_000,
  },
});
