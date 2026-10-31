import { expect, test, type Page } from "@playwright/test";
import { ASK_ID, REPLY, composerRow, expectWorking, install, openChat, setSrv, startRun } from "../chat-run-status-fixture";
import { captureContexts } from "./harness";

/**
 * GL-4 review rig: the chat in glass, and (with GL4_LOOK=classic) the classic
 * chat for the before/after pixel diff. Run by name, never committed as a gate:
 *
 *   GL4_GATE=glass UX_OUT=<dir> npx playwright test -c e2e/visual/gl4.config.ts glass-chat --project=visual
 *   GL4_LOOK=classic FREEZE=1 UX_OUT=<dir> npx playwright test -c e2e/visual/gl4.config.ts glass-chat --project=visual
 */
const OUT = process.env.UX_OUT || "ux-shots/glass-chat";
const CLASSIC = process.env.GL4_LOOK === "classic";
const ONLY = CLASSIC
  ? ["light-1440", "dark-1440", "mobile-390"]
  : ["light-1440", "dark-1440", "compact-1440", "accent-violet-1440", "mobile-390"];

async function freeze(page: Page) {
  if (!process.env.FREEZE) return;
  // Pixel diff only: stop live animation and hide the run timer.
  await page.addStyleTag({
    content:
      "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" +
      '[data-run-status="composer"] *{color:transparent!important} nextjs-portal{display:none!important}',
  });
  // Clock text changes between runs: hide a time of day and a duration.
  await page.evaluate(() => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (/^\s*(\d\d:\d\d [AP]M|\d+(\.\d)?m?s)\s*$/.test(n.textContent ?? "")) (n.parentElement as HTMLElement).style.visibility = "hidden";
    }
  });
  await page.waitForTimeout(300);
}

const shot = (page: Page, name: string) =>
  captureContexts(page, name, { out: OUT, only: ONLY, ...(CLASSIC ? {} : { look: "glass" as const }) });

test.describe.configure({ timeout: 240_000 });

test("a live run: glow, tool row, thinking", async ({ page }) => {
  await install(page);
  await startRun(page);
  await expectWorking(page, 5_000);
  await page.waitForTimeout(1_500);
  await freeze(page);
  await shot(page, "live");
});

test("a question waits: the tinted card", async ({ page }) => {
  await install(page, { ask: true });
  const box = await openChat(page);
  await box.fill("Plan the release");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Which project should I use?").first()).toBeVisible({ timeout: 20_000 });
  void ASK_ID;
  await page.waitForTimeout(1_200);
  await freeze(page);
  await shot(page, "ask");
});

test("a finished run: bubble, full-width answer", async ({ page }) => {
  await install(page);
  await startRun(page);
  await setSrv(page, { endsAt: Date.now() });
  await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(1_500);
  await freeze(page);
  void composerRow;
  await shot(page, "done");
});

test("a settled card: plain surface, green tile", async ({ page }) => {
  await install(page, { ask: true });
  const box = await openChat(page);
  await box.fill("Plan the release");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Which project should I use?").first()).toBeVisible({ timeout: 20_000 });
  await page.getByText("Apollo", { exact: true }).first().click();
  await page.getByRole("button", { name: /Submit/ }).click();
  await expect(page.getByText("Thanks, I will use Apollo.").first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(1_200);
  await freeze(page);
  await shot(page, "settled");
});

test("scroll to bottom: the glass button", async ({ page }) => {
  await install(page);
  await startRun(page);
  await setSrv(page, { endsAt: Date.now() });
  await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
  await page.setViewportSize({ width: 1440, height: 430 });
  await page.waitForTimeout(800);
  await page.getByText(REPLY).first().evaluate((el) => {
    let n: HTMLElement | null = el as HTMLElement;
    while (n && !(n.scrollHeight > n.clientHeight + 4 && getComputedStyle(n).overflowY === "auto")) n = n.parentElement;
    if (n) { n.scrollTop = 0; n.dispatchEvent(new Event("scroll")); }
  });
  await page.waitForTimeout(800);
  await freeze(page);
  if (!CLASSIC) {
    await page.evaluate(() => document.documentElement.setAttribute("data-look", "glass"));
    for (const mode of ["light", "dark"]) {
      await page.evaluate((m) => document.documentElement.classList.toggle("light", m === "light"), mode);
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${OUT}/scroll--glass-${mode}-1440.png` });
    }
  }
});

test("before the first word: typing dots", async ({ page }) => {
  await install(page);
  await page.addInitScript(() => {
    const inner = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/agent/chat")) await new Promise((r) => setTimeout(r, 120_000));
      return inner(input, init);
    };
  });
  const box = await openChat(page);
  await box.fill("Plan the release");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.waitForTimeout(2_000);
  await freeze(page);
  await shot(page, "dots");
});
