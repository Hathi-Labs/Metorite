import { expect, test } from "@playwright/test";

import {
  ASKING, ASK_NEXT, R2_HEAD, R2_PROMPT, R2_REPLY, REPLY, composerRow, expectWorking, install, openChat, setSrv, srv,
  startRun,
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

  test("(h) a run started elsewhere gets its own bubble, and the last reply stays as it was", async ({ page }) => {
    // Review of #1171, P1: R2 replayed into R1's finished reply and
    // overwrote it, and R2's prompt never showed.
    await install(page);
    await startRun(page);
    await setSrv(page, { endsAt: Date.now() });
    const r1 = page.locator("[data-rollup-turn]").filter({ hasText: REPLY });
    await expect(r1).toHaveCount(1, { timeout: 10_000 });
    // R1's words. (The step trail may fold by itself, so the test reads the
    // answer, not the whole bubble.)
    const r1Words = ["Let me get the rest of the tree.", REPLY];
    // Past the run state's hold after a terminal event (DONE_HOLD_MS).
    await page.waitForTimeout(11_000);
    await setSrv(page, { state: "running", endsAt: 0, r2At: Date.now() });
    // The poll names R2. Its prompt and its own reply bubble show.
    await expect(page.getByText(R2_PROMPT).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(R2_HEAD).first()).toBeVisible({ timeout: 15_000 });
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "working");
    // R1's reply is untouched, and R2 is not inside it.
    await expect(r1).toHaveCount(1);
    for (const w of r1Words) await expect(r1).toContainText(w);
    await expect(r1).not.toContainText(R2_HEAD);
    // R2's words sit in a bubble of their own, after its prompt.
    await expect(page.locator("[data-rollup-turn]").filter({ hasText: R2_HEAD })).toHaveCount(1);
    // R2 ends in its own bubble.
    await setSrv(page, { endsAt: Date.now() });
    await expect(page.getByText(R2_REPLY).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(REPLY)).toHaveCount(1);
    for (const w of r1Words) await expect(r1).toContainText(w);
    await expect(r1).not.toContainText(R2_REPLY);
  });

  test("(i) coming back to a chat draws no stale 'working' before the server answers", async ({ page }) => {
    // Review of #1171, P2: the row drew the kept "working" and Stop at once.
    await install(page);
    await startRun(page);
    // A reload: the stream is now a re-attach, which a chat switch aborts.
    await page.reload();
    await expectWorking(page, 30_000);
    // Keep the chat's row, to come back to it after its dot has gone.
    const rowHandle = await page.locator('[data-session-dot="running"]').first()
      .evaluateHandle((el) => el.closest('button,[role="button"],a,li') ?? el);
    // The run ends while the member is in another chat of the same page.
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await setSrv(page, { endsAt: Date.now() });
    await page.waitForTimeout(500);
    await page.evaluate(() => {
      const w = window as unknown as { __seen: { working: number } };
      w.__seen.working = 0;
    });
    await rowHandle.asElement()!.click();
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(1_000);
    const seen = await page.evaluate(() => (window as unknown as { __seen: { working: number } }).__seen.working);
    expect(seen, "a stale working row drew").toBe(0);
    await expect(page.locator('[data-chat-primary="stop"]')).toHaveCount(0);
  });

  test("(j) reload, switch away, R1 ends and R2 starts elsewhere, then come back", async ({ page }) => {
    // Re-review of #1171: the kept target of R1 took R2's replay.
    await install(page);
    await startRun(page);
    await page.reload();
    await expectWorking(page, 30_000);
    // R1's bubble: its words so far, or its final words once the server's
    // saved row merges in. Never R2's.
    const r1 = page.locator("[data-rollup-turn]").filter({ hasText: /Let me get the rest of the tree\.|Here is the full tree/ });
    await expect(r1).toHaveCount(1, { timeout: 15_000 });
    // Away: another chat of the same page aborts the re-attach.
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await page.waitForTimeout(500);
    // R1 ends, and a phone starts R2 in the same chat.
    await setSrv(page, { endsAt: Date.now() });
    await page.waitForTimeout(500);
    await setSrv(page, { state: "running", endsAt: 0, r2At: Date.now() });
    await page.evaluate(() => {
      (window as unknown as { __seen: { working: number } }).__seen.working = 0;
    });
    // After the reload the chat's row carries its title. Its bubble is not
    // on screen now, so the title is the only match.
    await page.getByText("Plan the release", { exact: true }).first().click();
    // R2's prompt shows, and R2 streams into a bubble of its own.
    await expect(page.getByText(R2_PROMPT).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(R2_HEAD).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("[data-rollup-turn]").filter({ hasText: R2_HEAD })).toHaveCount(1);
    await expect(r1).toHaveCount(1);
    await expect(r1).not.toContainText(R2_HEAD);
    await setSrv(page, { endsAt: Date.now() });
    await expect(page.getByText(R2_REPLY).first()).toBeVisible({ timeout: 15_000 });
    await expect(r1).not.toContainText(R2_REPLY);
    await expect(page.locator("[data-rollup-turn]").filter({ hasText: R2_REPLY })).toHaveCount(1);
  });

  test("(k) on a return, no Stop and no 'working…' before the server lists the run", async ({ page }) => {
    // Re-review nit: a remount's "checking" offered Stop for one poll.
    await install(page);
    await startRun(page);
    await page.reload();
    await expectWorking(page, 30_000);
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await page.waitForTimeout(500);
    // Record whether Stop ever draws while the run is not listed.
    await setSrv(page, { endsAt: Date.now() });
    await page.evaluate(() => {
      const w = window as unknown as { __stopSeen: number };
      w.__stopSeen = 0;
      new MutationObserver(() => {
        if (document.querySelector('[data-chat-primary="stop"]')) w.__stopSeen++;
      }).observe(document, { subtree: true, childList: true, attributes: true });
    });
    // After the reload the chat's row carries its title. Its bubble is not
    // on screen now, so the title is the only match.
    await page.getByText("Plan the release", { exact: true }).first().click();
    await expect(page.getByRole("textbox", { name: /^Message / })).toBeVisible();
    await page.waitForTimeout(6_000);
    expect(await page.evaluate(() => (window as unknown as { __stopSeen: number }).__stopSeen)).toBe(0);
    await expect(page.getByText("working…")).toHaveCount(0);
  });

  test("(l) a start time that moves between two lists, then a proxy cut, draws ONE bubble", async ({ page }) => {
    // Re-review of #1171: an older gateway registers each run twice, and the
    // second register moves startedAt. With no run id, a list in that gap
    // read the member's own run as a new one. After the proxy cut, the
    // re-attach then replayed the whole run into a second bubble.
    await install(page, { noRunId: true, shiftAfterLists: 2, shiftMs: 750, cutAfterMs: 14_000 });
    await startRun(page);
    // Two lists with the first start time, then the moved one, all while
    // the stream reads. Then the "proxy" cuts the stream, and the chat
    // re-attaches.
    await expect.poll(async () => (await srv(page)).lists, { timeout: 30_000 }).toBeGreaterThanOrEqual(3);
    await expect.poll(async () => (await page.evaluate(() => (window as unknown as { __calls: string[] }).__calls))
      .filter((c) => c === "reconnect").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
    await expectWorking(page, 10_000);
    const head = page.locator("[data-rollup-turn]").filter({ hasText: "Let me get the rest of the tree." });
    await expect(head).toHaveCount(1);
    await setSrv(page, { endsAt: Date.now(), cutAfterMs: 0 });
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(1_500);
    // Exactly one reply bubble for the run.
    await expect(head).toHaveCount(1);
    await expect(page.locator("[data-rollup-turn]").filter({ hasText: REPLY })).toHaveCount(1);
  });

  test("(m) a question answered, then a chat switch mid-run: it draws as answered, with no second answer and no steer", async ({ page }) => {
    // Review of #1171, P1: the log drew the answered question again as
    // waiting, and a second answer reached the agent as a steer.
    await install(page, { ask: true });
    const box = await openChat(page);
    await box.fill("Plan the release");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    // The card waits: the row says so, not "working".
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "waiting", { timeout: 15_000 });
    await expect(page.getByText("Which project should I use?").first()).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Apollo" }).click();
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect(page.getByText(ASK_NEXT).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-elicitation-card="answered"]')).toHaveCount(1);
    // Away and back, while the run goes on.
    const running = page.locator('[data-session-dot="running"]').first();
    await expect(running).toBeVisible();
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await page.waitForTimeout(800);
    await page.locator('[data-session-dot="running"]').first().click();
    await expect(page.getByText(ASK_NEXT).first()).toBeVisible({ timeout: 15_000 });
    // The question is answered, with its answer, and nothing waits.
    await expect(page.locator('[data-elicitation-card="answered"]')).toHaveCount(1);
    await expect(page.locator('[data-elicitation-card="open"]')).toHaveCount(0);
    await expect(page.locator('[data-elicitation-card="answered"]')).toContainText("Apollo");
    await expect(composerRow(page)).not.toHaveAttribute("data-run-tone", "waiting");
    await page.waitForTimeout(1_500);
    // One answer reached respond-input, and nothing steered the run.
    const answers = await page.evaluate(() => (window as unknown as { __answers?: unknown[] }).__answers ?? []);
    expect(answers).toHaveLength(1);
    const calls = await page.evaluate(() => (window as unknown as { __calls: string[] }).__calls);
    expect(calls.filter((c) => c === "send")).toHaveLength(1);
  });

  test("(n) two parallel delegations: one asks while the other streams, a chat switch, the card still waits", async ({ page }) => {
    // Review of #1171: the sibling's sub-agent events marked the waiting
    // question as answered, and after the remount no card showed.
    await install(page, { ask: true, parallel: true });
    const box = await openChat(page);
    await box.fill("Plan the release");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("Which project should I use?").first()).toBeVisible({ timeout: 15_000 });
    // B streams on while A waits.
    await expect(page.locator('[data-elicitation-card="open"]')).toHaveCount(1);
    await page.waitForTimeout(1_000);
    // Away and back, while the question waits.
    await page.getByRole("button", { name: /New conversation/ }).click();
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
    await page.waitForTimeout(800);
    await page.locator('[data-session-dot="running"]').first().click();
    // The card still waits, and the row says so.
    await expect(page.locator('[data-elicitation-card="open"]')).toHaveCount(1, { timeout: 15_000 });
    await expect(page.getByText("Which project should I use?").first()).toBeVisible();
    await expect(composerRow(page)).toHaveAttribute("data-run-tone", "waiting", { timeout: 15_000 });
    // One answer, and the run goes on.
    await page.getByRole("button", { name: "Apollo" }).click();
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect(page.getByText(ASK_NEXT).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-elicitation-card="answered"]')).toHaveCount(1);
    await page.waitForTimeout(1_000);
    const answers = await page.evaluate(() => (window as unknown as { __answers?: unknown[] }).__answers ?? []);
    expect(answers).toHaveLength(1);
    const calls = await page.evaluate(() => (window as unknown as { __calls: string[] }).__calls);
    expect(calls.filter((c) => c === "send")).toHaveLength(1);
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
