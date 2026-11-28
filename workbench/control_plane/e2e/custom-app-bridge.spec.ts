import { expect, test, type Frame, type Page } from "@playwright/test";
import { build } from "esbuild";
import path from "node:path";

/**
 * The Custom App bridge answers ONLY its own app frame (task 2433, F2 of
 * STU-L0).
 *
 * THE DEFECT this fences. `useCcBridge` answered a `__ccsdk` request from ANY
 * window on the page. It read only `data.slug`, and a message with no slug
 * passed too. A chat artifact frame on the same page (the assistant panel is
 * on every page) could then call `storage.*`, `ai.complete` and `tools.call`
 * of the open app as the viewer.
 *
 * The real `SandboxedHtml` and the real `ccBridge.ts` are bundled with
 * esbuild and run in Chromium, as `untrusted-html.spec.ts` does. The page
 * holds the app frame and one artifact frame. Each case sends a request to a
 * table of its own, and `page.route` records which App Runtime calls the
 * bridge made. A refused request makes no call.
 */

// Playwright runs from `workbench/control_plane`, beside its config.
const ROOT = process.cwd();
const SLUG = "demo";
const NONCE = "n-0123456789abcdef0123456789abcdef";

/** The app: calls `cc.user()` at load and prints the answer. */
const APP_HTML = `<!doctype html><html><head></head><body>
<div id="out">waiting</div>
<script>
  window.addEventListener("load", function () {
    cc.user().then(
      function (r) { document.getElementById("out").textContent = "me:" + r.email; },
      function (e) { document.getElementById("out").textContent = "err:" + e.message; }
    );
  });
</script></body></html>`;

/** A chat artifact on the same page. It does nothing until a case drives it. */
const ARTIFACT_HTML = `<div id="artifact">artifact</div>`;

/** The page that the app frame navigates itself to. It never had the nonce. */
const EVIL_HTML = `<!doctype html><html><body><script>
  parent.postMessage({ __ccsdk: true, id: "evil-1", method: "storage.list",
    args: ["case-selfnav", {}], slug: ${JSON.stringify(SLUG)}, mode: "live" }, "*");
</script></body></html>`;

const ENTRY = `
import { createElement as h, useCallback, useMemo, useRef } from "react";
import { createRoot } from "react-dom/client";
import SandboxedHtml from "@/components/SandboxedHtml";
import { buildAppSrcDoc, useCcBridge } from "@/app/build/apps/lib/ccBridge";

const H = window.__harness;

function Page() {
  const appFrameRef = useRef(null);
  const getFrame = useCallback(() => appFrameRef.current, []);
  useCcBridge(H.slug, { mode: "live", nonce: H.nonce, getFrame });
  const srcDoc = useMemo(
    () => buildAppSrcDoc(H.appHtml, { slug: H.slug, mode: "live", nonce: H.nonce }),
    [],
  );
  return h("div", null,
    h("div", { id: "app" }, h(SandboxedHtml, { chromeless: true, html: srcDoc, iframeRef: appFrameRef, height: 120 })),
    h("div", { id: "art" }, h(SandboxedHtml, { html: H.artifactHtml, height: 80 })),
  );
}

createRoot(document.getElementById("root")).render(h(Page));
`;

let bundle = "";

test.beforeAll(async () => {
  const out = await build({
    stdin: { contents: ENTRY, resolveDir: path.join(ROOT, "src"), loader: "tsx" },
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
    jsx: "automatic",
    alias: { "@": path.join(ROOT, "src") },
    define: { "process.env.NODE_ENV": '"development"' },
    logLevel: "silent",
  });
  bundle = out.outputFiles[0].text;
});

/** Each App Runtime call the bridge made, as `METHOD path`. */
async function open(page: Page): Promise<string[]> {
  const calls: string[] = [];
  await page.route(`**/api/apps/${SLUG}/**`, async (route) => {
    const url = new URL(route.request().url());
    calls.push(`${route.request().method()} ${url.pathname}`);
    const body = url.pathname.endsWith("/me")
      ? { email: "viewer@example.com" }
      : { rows: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route("**/__evil-page", (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: EVIL_HTML }),
  );
  const harness = JSON.stringify({
    slug: SLUG, nonce: NONCE, appHtml: APP_HTML, artifactHtml: ARTIFACT_HTML,
  }).replace(/</g, "\\u003c");
  const html =
    `<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div>` +
    `<script>window.__harness = ${harness};</script>` +
    `<script>${bundle.replace(/<\/(script)/gi, "<\\/$1")}</script></body></html>`;
  await page.route("**/__ccbridge-probe", (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: html }),
  );
  await page.goto("/__ccbridge-probe");
  // The real app works: its own `cc.user()` call is answered.
  const app = await appFrame(page);
  await expect.poll(() => app.evaluate(() => document.getElementById("out")?.textContent)).toBe(
    "me:viewer@example.com",
  );
  return calls;
}

async function frameWith(page: Page, selector: string): Promise<Frame> {
  const handle = await page.locator(selector).elementHandle();
  const frame = await handle?.contentFrame();
  if (!frame) throw new Error(`no frame for ${selector}`);
  return frame;
}

const appFrame = (page: Page) => frameWith(page, "#app iframe");
const artifactFrame = (page: Page) => frameWith(page, "#art iframe");

/** Post one raw bridge request from inside `frame`. */
async function send(frame: Frame, msg: Record<string, unknown>): Promise<void> {
  await frame.evaluate((m) => parent.postMessage(m, "*"), msg);
}

function request(table: string, extra: Record<string, unknown>): Record<string, unknown> {
  return { __ccsdk: true, id: `t-${table}`, method: "storage.list", args: [table, {}], mode: "live", ...extra };
}

/** Wait long enough for a bridge that WOULD answer to have made its call. */
async function settle(page: Page): Promise<void> {
  await page.waitForTimeout(800);
}

test("the repro: a chat artifact frame cannot drive the app's bridge", async ({ page }) => {
  const calls = await open(page);
  const art = await artifactFrame(page);
  await send(art, request("case-artifact", { slug: SLUG }));
  await send(art, request("case-artifact-noslug", {}));
  await settle(page);
  expect(calls.filter((c) => c.includes("/data/case-artifact"))).toEqual([]);
});

test("a wrong window is refused even when it holds the right slug and nonce", async ({ page }) => {
  const calls = await open(page);
  const art = await artifactFrame(page);
  await send(art, request("case-wrong-window", { slug: SLUG, nonce: NONCE }));
  await settle(page);
  expect(calls.filter((c) => c.includes("/data/case-wrong-window"))).toEqual([]);
});

test("the app's own frame: no slug, a wrong slug or a wrong nonce is refused", async ({ page }) => {
  const calls = await open(page);
  const app = await appFrame(page);
  await send(app, request("case-no-slug", { nonce: NONCE }));
  await send(app, request("case-wrong-slug", { slug: "other", nonce: NONCE }));
  await send(app, request("case-wrong-nonce", { slug: SLUG, nonce: "n-guess" }));
  await send(app, request("case-no-nonce", { slug: SLUG }));
  // The control: the same frame with the right slug and nonce is answered.
  await send(app, request("case-good", { slug: SLUG, nonce: NONCE }));
  await expect.poll(() => calls.filter((c) => c.includes("/data/"))).toEqual([
    `GET /api/apps/${SLUG}/data/case-good`,
  ]);
});

test("a frame that navigates itself loses the bridge", async ({ page }) => {
  const calls = await open(page);
  const app = await appFrame(page);
  await app.evaluate(() => {
    location.href = "/__evil-page";
  });
  await page.waitForTimeout(1200);
  expect(calls.filter((c) => c.includes("/data/case-selfnav"))).toEqual([]);
});
