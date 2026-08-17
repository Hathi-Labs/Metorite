import { expect, test, type Page } from "@playwright/test";
import { deflateSync, crc32 } from "node:zlib";

/**
 * Organization branding, end to end (WS-32 · OI-1 / OI-3a).
 *
 * ## Why this is an e2e test and not a unit test
 *
 * `AGENTS.md` is blunt that nothing in this tree tests layout: the conformance
 * suite checks eight regexes and stops, so the real gate on a UI change is
 * `DESIGN_SYSTEM.md` §8 — switch to Fluent, then Material, then Graphite, and
 * *look*. The brand lockup is the surface where that matters most, because it
 * is the one element whose size is decided by a **customer-supplied image** in
 * a fixed 256px rail. Nothing about that is expressible as a regex.
 *
 * Its history is the argument. The lockup shipped rendering
 * "powered by Co…" — truncated — while every unit test passed and every box
 * measurement agreed, because `truncate` clips text without changing any
 * bounding box. `captionClipped` below is read off `scrollWidth` for exactly
 * that reason, and it is asserted on a **leaf** span: the first version of this
 * check searched every span, matched the outer flex container (which is never
 * truncated) and so could not fail in the case it was written for.
 *
 * ## Why it lives here
 *
 * This began as two standalone scripts under `scripts/` with a hardcoded
 * `/opt/pw-browsers/...` path and its own port. That was a **second Playwright
 * seam** beside this directory, `playwright.config.ts` and `run-e2e.mjs`
 * (CLAUDE.md §4: extend the shared seam, never add a parallel one), and the
 * hardcoded POSIX path is the precise thing `run-e2e.mjs` exists to avoid on
 * the Windows primary dev box. Folded in, it inherits the browser resolution,
 * the base URL and the suite runner for free.
 */

// ── Fixtures ───────────────────────────────────────────────────────────────

/**
 * A 600×160 (3.75:1) solid PNG — the shape of a real wordmark, in a colour that
 * belongs to no theme so its bounds are unmistakable in a failure screenshot.
 *
 * Generated rather than committed: 568 bytes of zlib is easier to audit than a
 * binary blob, and the aspect ratio is the part actually under test.
 */
function wordmarkPng(width = 600, height = 160, rgb: [number, number, number] = [232, 74, 138]): string {
  const raw = Buffer.concat(
    Array.from({ length: height }, () =>
      Buffer.concat([
        Buffer.from([0]), // filter byte: none
        Buffer.from(Array.from({ length: width }, () => rgb).flat()),
      ]),
    ),
  );
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]).toString("base64");
}

const LOGO_B64 = wordmarkPng();

const BRANDING_WITH_LOGO = {
  logo: {
    dataUri: `data:image/png;base64,${LOGO_B64}`,
    mime: "image/png",
    width: 600,
    height: 160,
    byteSize: 568,
  },
  updatedBy: "admin@example.com",
  updatedAt: "2026-08-14T00:00:00+00:00",
};

const NO_BRANDING = { logo: null, updatedBy: "", updatedAt: "" };
const ADMIN = { email: "admin@example.com", is_admin: true, features: [], permissions: ["*"], roles: ["admin"] };

const THEMES = ["rapidtool", "fluent", "material", "graphite"] as const;
const MODES = ["dark", "light"] as const;

/**
 * Stub the shell's API surface with ONE handler that switches on the path.
 *
 * ⚠️ **Deliberately not several overlapping `page.route` patterns.** Whether a
 * broad `**` pattern or a specific one wins is a function of registration order
 * *and* the direction Playwright walks its handler list — and getting it
 * backwards fails in the worst possible way: the catch-all answers `[]`, the
 * component reads that as "this org has no logo", the fallback renders, and the
 * assertions that matter never run while the suite reports green. That happened
 * twice — once in the standalone script this file replaces, and once here after
 * the ordering was supposedly fixed.
 *
 * A single router has no ordering semantics to get wrong. `branding` is served
 * by an explicit branch; anything unmatched gets the empty list.
 */
async function stubApi(page: Page, branding: unknown, onBranding?: () => Promise<void>) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/settings/branding") {
      if (onBranding) await onBranding();
      return json(branding);
    }
    if (path === "/api/auth/me") return json(ADMIN);
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
}

async function loadWithTheme(page: Page, theme: string, mode: string, path = "/settings/organization") {
  await page.addInitScript(
    ([t, m]) => {
      localStorage.setItem("cc-theme", t);
      localStorage.setItem("theme", m);
    },
    [theme, mode],
  );
  await page.goto(path);
}

/** Everything the eyeball check is actually looking at, as numbers. */
async function measureLockup(page: Page) {
  return page.evaluate(() => {
    const rail = document.querySelector("aside");
    const link = rail?.querySelector<HTMLElement>("a[href='/']");
    const img = link?.querySelector("img");
    const collapse = rail?.querySelector("button[title]");
    const box = (el: Element | null | undefined) => (el ? el.getBoundingClientRect() : null);

    // LEAF spans only — a container is never truncated, so matching one makes
    // the clip check unfalsifiable. This is the bug this file documents.
    const leaves = link
      ? [...link.querySelectorAll("span")].filter((e) => !e.querySelector("span"))
      : [];
    const caption = leaves.find((e) => {
      const t = e.textContent?.trim() ?? "";
      return t.startsWith("powered by Metorite") || t === "Control Plane" || t === "Home";
    });

    return {
      railWidth: box(rail)?.width ?? 0,
      lockupRight: Math.round(box(link)?.right ?? 0),
      collapseLeft: Math.round(box(collapse)?.left ?? 0),
      collapseRight: Math.round(box(collapse)?.right ?? 0),
      logo: img ? { w: Math.round(img.getBoundingClientRect().width), h: Math.round(img.getBoundingClientRect().height) } : null,
      text: link?.textContent?.trim() ?? "",
      captionFound: !!caption,
      captionClipped: caption ? caption.scrollWidth > caption.clientWidth + 1 : null,
      pageScrollsX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// ⚠️ EVERY TEST IN THIS FILE IS `fixme` — AUTHORED, NOT RUNNING, AND HERE IS WHY
//
// Measured 2026-08-14 against the dev webServer this config now uses: the page
// **never hydrates** under Playwright. Zero `/api/**` requests are issued — not
// even `/api/auth/me` — while the browser console repeats
//   WebSocket connection to 'ws://127.0.0.1:3101/_next/webpack-hmr' failed
// So React effects never run, the branding fetch never happens, and the shell
// sits on the server-rendered fallback for ever.
//
// ⚠️ **THE PARTIAL PASS WAS THE DANGEROUS PART.** On the run that produced this
// note, 9 of 18 tests passed — and every one of them was a fallback or outage
// case, which asserts precisely what the un-hydrated page already renders.
// They would pass against an app whose client bundle was deleted. A green half
// of a suite that cannot execute its subject is worse than a red one, so the
// whole file is marked rather than left to report a number that means nothing.
//
// What IS fixed and is worth keeping: the suite now BOOTS. Under the previous
// `next start` webServer it could not, because CP-0's fail-closed auth answers
// 503 without auth config and the readiness probe never went green — see
// `playwright.config.ts`. That was true for every spec in this directory, not
// just this one.
//
// ── MEASURED 2026-08-14. Three of the four hypotheses are DEAD. ────────────
//
// The measurement this note used to ask for was run. Recorded as eliminations
// so nobody spends another session on a dead end:
//
//   ✗ Playwright's request interception — loading with ZERO routes registered
//     gives the identical result (0 `/api/**` requests, no hydration). The
//     interception is innocent.
//   ✗ The container's HTTP proxy — launching Chromium with `--no-proxy-server`
//     changes nothing. (`proxy: {server: "direct://"}` merely breaks navigation
//     with ERR_PROXY_CONNECTION_FAILED; it is not a control, ignore it.)
//   ✗ A truncated RSC stream — `/chat` answers 200 in ~94ms, 95 KB, closes
//     cleanly, ends in `</body></html>` and carries its `__next_f.push`
//     payload. The server half is complete.
//
// What the page actually does: 39 JS chunks load, **zero requests fail**, no
// page errors, `window.next` and the React DevTools hook are both defined — so
// React creates a root and attaches event delegation (5 elements carry
// `__reactEvents`). But of 422 elements **zero carry `__reactFiber` or
// `__reactProps`**, and `localStorage` is completely empty, so not even the
// theming engine's mount effect ran. The root is created; the tree is never
// hydrated.
//
// ⚠️ `__reactEvents` ALONE IS NOT HYDRATION, and reading it as such briefly
// produced the opposite conclusion here. Count `__reactFiber` across the whole
// tree; a handful of `__reactEvents` on the container is what an UN-hydrated
// React root looks like.
//
// ► THE ONE HYPOTHESIS LEFT, with a mechanism: the dev runtime's HMR socket.
//   The client opens `ws://<host>/_next/webpack-hmr` and the handshake fails
//   with `ERR_INVALID_HTTP_RESPONSE`, while the server is **Next 16.2.6 on
//   Turbopack** and the chunk it loaded is `[turbopack]_browser_dev_hmr-client`
//   — a webpack-shaped endpoint against a Turbopack dev server. Whether that
//   failure is the CAUSE of the stalled hydration or merely its loudest
//   symptom is the next thing to measure, and it is genuinely open: a dead HMR
//   socket does not normally block hydration, which is why this is stated as a
//   lead and not a diagnosis.
// ─────────────────────────────────────────────────────────────────────────────

test.fixme(
  true,
  "the app does not hydrate under the dev webServer — see the banner above",
);

// ── The lockup, under every theme ──────────────────────────────────────────

for (const theme of THEMES) {
  for (const mode of MODES) {
    test(`brand lockup renders a customer logo under ${theme}/${mode}`, async ({ page }) => {
      await stubApi(page, BRANDING_WITH_LOGO);
      await loadWithTheme(page, theme, mode);
      await page.waitForSelector("aside img", { timeout: 30_000 });

      const m = await measureLockup(page);

      expect(m.railWidth, "the rail is a fixed 256px").toBe(256);
      // 600×160 at 28px tall wants 105px. A square mark must NOT get a
      // wordmark's width, which is what makes this an equality not a bound.
      expect(m.logo).toEqual({ w: 105, h: 28 });
      expect(m.text).toContain("powered by Metorite");
      // The customer's mark replaces ours; it does not sit next to it.
      expect(m.text).not.toContain("Control Plane");

      expect(m.captionFound, "the attribution span must be findable").toBe(true);
      expect(m.captionClipped, "the attribution must not truncate").toBe(false);

      expect(m.lockupRight, "the lockup must not overrun the collapse control")
        .toBeLessThanOrEqual(m.collapseLeft);
      expect(m.collapseRight, "the collapse control must stay on the rail")
        .toBeLessThanOrEqual(m.railWidth);
      expect(m.pageScrollsX, "the page body must never scroll horizontally").toBe(false);
    });

    test(`brand lockup falls back to our mark under ${theme}/${mode}`, async ({ page }) => {
      await stubApi(page, NO_BRANDING);
      await loadWithTheme(page, theme, mode);
      await page.waitForSelector("aside a[href='/']", { timeout: 30_000 });

      const m = await measureLockup(page);

      // An org that has uploaded nothing gets our mark DELIBERATELY — the
      // failure this guards is an empty box where a logo would be.
      expect(m.logo, "no <img> for an org with no logo").toBeNull();
      expect(m.text).toContain("Metorite");
      expect(m.captionClipped).toBe(false);
      expect(m.lockupRight).toBeLessThanOrEqual(m.collapseLeft);
      expect(m.pageScrollsX).toBe(false);
    });
  }
}

// ── OI-3a: the first paint must not wait on the network ────────────────────

test("a returning member's logo paints without waiting for the branding call", async ({ page }) => {
  // The endpoint is held deliberately slow. Anything that still depends on it
  // shows up as a timeout rather than hiding behind a fast local mock.
  const SLOW_MS = 3_000;
  let calls = 0;

  await stubApi(page, BRANDING_WITH_LOGO, async () => {
    calls++;
    await new Promise((res) => setTimeout(res, SLOW_MS));
  });

  // First visit: nothing cached, so waiting IS correct here.
  await loadWithTheme(page, "rapidtool", "dark");
  await page.waitForSelector("aside img", { timeout: 20_000 });
  expect(calls, "the first visit must actually fetch").toBeGreaterThan(0);

  // Second visit: the cache should paint it before the slow call returns.
  const started = Date.now();
  await page.goto("/settings/organization");
  await page.waitForSelector("aside img", { timeout: 20_000 });
  const elapsed = Date.now() - started;

  expect(
    elapsed,
    `warm paint took ${elapsed}ms with a ${SLOW_MS}ms endpoint — it is still gated on the fetch`,
  ).toBeLessThan(SLOW_MS);
});

test("a branding outage leaves the shell rendering, not broken", async ({ page }) => {
  // There is nothing a member can do about this, so it must degrade to our own
  // mark silently rather than surface an error in the app shell.
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/settings/branding") return route.fulfill({ status: 503, body: "{}" });
    if (path === "/api/auth/me") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ADMIN) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await loadWithTheme(page, "rapidtool", "dark");
  await page.waitForSelector("aside a[href='/']", { timeout: 30_000 });

  const m = await measureLockup(page);
  expect(m.text).toContain("Metorite");
  expect(m.pageScrollsX).toBe(false);
});
