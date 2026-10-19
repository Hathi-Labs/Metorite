import { expect, test } from "@playwright/test";

import {
  ASKING, REPLY, composerRow, expectWorking, install, openChat, setSrv, srv, startRun,
} from "./chat-run-status-fixture";

/**
 * The owner's report of 2026-10-19, in a real browser against the real chat
 * page: "if an agent session is running, it must appropriately show in the
 * chatbox … sometimes I just see a thinking container for a fleeting moment
 * before it disappears."
 *
 * ONE run state drives every indicator (`lib/runStatus.ts`, spec
 * `chat_run_continuity.md` §4.10). This file holds the visible contract:
 *
 *   (a) a long silent tool call keeps the status row up the whole time;
 *   (b) a reload mid-run shows the status again within about a second, and
 *       the stream re-attaches;
 *   (c) the same after a move to another app and back;
 *   (d) the same after a switch to another chat and back;
 *   (e) a run that ends while the page is away ends as done, with no stuck
 *       spinner;
 *   (f) waiting for you, parked and working draw differently;
 *   (g) a run state shorter than 400 ms flashes no row and no thinking
 *       container.
 *
 * The "server" lives in `sessionStorage`, so it outlives a reload, as the
 * gateway does. No gateway runs under this suite: every request the page
 * makes has a stub here, or fails at once.
 */

test.describe("One predictable run status (§4.10)", () => {
  test.describe.configure({ timeout: 150_000 });

  test("(a) a long silent tool call keeps the status visible the whole time", async ({ page }) => {
    await install(page);
    await startRun(page);
    // The run sends nothing for a long while. Sample the row every second.
    // A 20 s window holds four server polls and ten ticks of the run state.
    for (let i = 0; i < 20; i++) {
      await expect(composerRow(page)).toHaveAttribute("data-run-tone", "working");
      await expect(composerRow(page)).toContainText(ASKING);
      await page.waitForTimeout(1_000);
    }
    await expect(page.locator('[data-chat-primary="stop"]')).toBeVisible();
    // The elapsed time counts.
    await expect(composerRow(page)).toContainText(/· \d+s|· \d+m/);
    // The run ends: the reply shows, and the row says Done.
    await setSrv(page, { endsAt: Date.now() });
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 10_000 });
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "done");
    await expect(page.locator('[data-chat-primary="stop"]')).toHaveCount(0);
  });

  test("(a2) a stream the proxy cuts mid-run re-attaches and keeps the status", async ({ page }) => {
    await install(page, { cutAfterMs: 2_000 });
    await startRun(page);
    // The stream closes with no terminal event after 2 s. The run goes on.
    await page.waitForTimeout(4_000);
    await expectWorking(page);
    await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __calls: string[] }).__calls))
      .filter((c) => c === "reconnect").length, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
    await expectWorking(page);
    await setSrv(page, { endsAt: Date.now(), cutAfterMs: 0 });
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
  });

  test("(b) a reload mid-run shows the status within a second, and re-attaches", async ({ page }) => {
    await install(page);
    await startRun(page);
    await page.reload();
    await expect(page.getByRole("textbox", { name: /^Message / })).toBeVisible({ timeout: 60_000 });
    const t0 = Date.now();
    await expectWorking(page, 5_000);
    // About a second once the page is up (a dev build compiles on load).
    expect(Date.now() - t0).toBeLessThan(5_000);
    await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __calls: string[] }).__calls))
      .includes("reconnect")).toBe(true);
    await expect(composerRow(page)).toContainText(ASKING);
    await setSrv(page, { endsAt: Date.now() });
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-chat-primary="stop"]')).toHaveCount(0, { timeout: 10_000 });
  });

  test("(c) a move to another app and back keeps the status", async ({ page }) => {
    await install(page);
    await startRun(page);
    await page.goto("/tasks");
    await page.waitForTimeout(1_000);
    await page.goBack();
    await expectWorking(page, 30_000);
    await expect(composerRow(page)).toContainText(ASKING);
  });

  test("(d) a switch to another chat and back keeps the status", async ({ page }) => {
    await install(page);
    await startRun(page);
    // Another chat, then this one again, in the same page.
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await expect(page.locator('[data-run-tone="working"]')).toHaveCount(0, { timeout: 10_000 });
    await page.waitForTimeout(1_000);
    // The list marks the running chat with its dot, from the same sources.
    const runningRow = page.locator('[data-session-dot="running"]').first();
    await expect(runningRow).toBeVisible();
    await runningRow.click();
    await expectWorking(page, 10_000);
    await expect(composerRow(page)).toContainText(ASKING);
  });

  test("(e) a run that ends while the page is away ends as done, with no stuck spinner", async ({ page }) => {
    await install(page);
    await startRun(page);
    await page.goto("/tasks");
    await setSrv(page, { endsAt: Date.now() });
    await page.waitForTimeout(1_000);
    await page.goBack();
    await expect(page.getByRole("textbox", { name: /^Message / })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-chat-primary="stop"]')).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator('[data-run-tone="working"]')).toHaveCount(0);
  });

  test("(f) waiting for you, parked and working draw differently", async ({ page }) => {
    await install(page);
    await startRun(page);
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "working");
    const working = await composerRow(page).innerText();

    await setSrv(page, { state: "needs_input", live: true });
    await page.reload();
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "waiting", { timeout: 30_000 });
    const waiting = await composerRow(page).innerText();

    await setSrv(page, { state: "needs_input", live: false });
    await page.reload();
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "parked", { timeout: 30_000 });
    const parked = await composerRow(page).innerText();
    // A parked run has nothing to stop. The composer sends.
    await expect(page.locator('[data-chat-primary="stop"]')).toHaveCount(0);

    expect(new Set([working, waiting, parked]).size).toBe(3);
  });

  test("(g) a run state shorter than 400 ms flashes no row and no thinking container", async ({ page }) => {
    await install(page);
    const box = await openChat(page);
    await box.fill("Plan the release");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(composerRow(page)).toContainText(ASKING, { timeout: 15_000 });
    const thread = (await srv(page)).threadId!;
    // The run ends at once, and the page reloads into the very end of it: the
    // server lists it for under 400 ms.
    await setSrv(page, { endsAt: Date.now() + 250 });
    // The observer starts fresh on each load (`addInitScript`).
    await page.reload();
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(1_500);
    const seen = await page.evaluate(() => (window as unknown as { __seen: { working: number; thinking: number } }).__seen);
    expect(seen.working, "a working row flashed").toBe(0);
    expect(seen.thinking, "a live thinking container flashed").toBe(0);
    expect(thread).toBeTruthy();
  });
});
