import { expect, test, type Page } from "@playwright/test";

/**
 * The owner's requests of 2026-10-14, in a real browser: the one assistant
 * opens and closes as the left sidebar folds, and it changes to the full view
 * and back with a light transition (`lib/panelMotion.ts`).
 *
 * This spec checks the END states and never a timing. A transition only
 * delays what each step asserts, and Playwright waits for it.
 *
 * - The side panel opens with the composer focused. The page beside it gives
 *   up the panel's width (the spacer), as it does for the sidebar.
 * - A close leaves the panel mounted, hidden and inert, and focus goes back
 *   to the toggle.
 * - A live run survives a close, a reopen, the full view and the way back.
 *   The chat element is never remounted, and the held stream ends in it.
 * - Reduced motion lands in the same states.
 * - A narrow desktop floats the panel, with no spacer. A phone opens it as a
 *   sheet from the Ask tab, and a close gives focus back to the tab.
 * - With the chat-first flag on (CF-1), the phone sheet drops to the peek
 *   strip above the bottom nav, by its chevron and by a pill link in the
 *   chat. A tap on the strip opens the sheet, with the same chat. With the
 *   flag off, the sheet has no way down, and a pill link closes it.
 *
 * The vitest twin is `src/lib/panelMotion.test.ts`. No gateway runs here:
 * each request the page makes has a stub, or fails at once.
 */

/** A task the stubbed answer names, so the chat draws a task pill (`ChatTaskRef`). */
const TASK_ID = "00000000-0000-4000-8000-000000000171";

async function install(page: Page, opts: { chatFirst?: boolean; pill?: boolean } = {}) {
  await page.addInitScript(({ chatFirst, pill, taskId }) => {
    window.localStorage.clear();
    // The one assistant needs its flag and the full frame (`chatFlag.ts`).
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
    // CF-1: the chat-first flag, through its dev override. Off unless asked.
    if (chatFirst) window.localStorage.setItem("cc-shell-chat-first", "1");
    const w = window as unknown as Record<string, unknown>;
    let open: (() => void) | null = null;
    w.__release = () => open?.();
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/chat/active-sessions")) return json([]);
      // The lookup that turns a bare "#171" into a task pill.
      if (pill && url.includes("/api/projects/search/refs")) {
        return json({
          rows: [{ id: taskId, task_number: 171, title: "Plan the release", status_name: "To do", category: "todo", archived: false }],
        });
      }
      if (pill && url.includes("/api/projects/")) return json({ rows: [], items: [], total: 0, children: [] });
      if (url.includes("/api/auth/me")) {
        return json({
          email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat"],
          features_denied: [], agents: ["orchestrator"], permissions: ["*"],
          capabilities: [], denied: [], is_admin: true,
        });
      }
      if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
      if (url.includes("/api/chat/sessions")) {
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("/room")) return json({ detail: "not a room" }, 404);
        return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        if (pill) {
          // One whole answer that names a task, as the orchestrator echoes it.
          const done = new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              c.enqueue(frame({ type: "delta", content: "Next up is #171 Plan the release.", messageId: "m-1" }));
              c.enqueue(frame({ type: "message_end", messageId: "m-1" }));
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(done, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        // The run holds after its first words, until the spec releases it.
        const gate = new Promise<void>((r) => { open = r; });
        const stream = new ReadableStream<Uint8Array>({
          async start(c) {
            c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            c.enqueue(frame({ type: "delta", content: "Working on the plan. ", messageId: "m-1" }));
            await gate;
            c.enqueue(frame({ type: "delta", content: "The due date is Friday.", messageId: "m-1" }));
            c.enqueue(frame({ type: "message_end", messageId: "m-1" }));
            c.enqueue(frame({ type: "done" }));
            c.close();
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { chatFirst: Boolean(opts.chatFirst), pill: Boolean(opts.pill), taskId: TASK_ID });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

const panel = (page: Page) => page.locator("section[data-assistant]");
const spacer = (page: Page) => page.locator("[data-assistant-spacer]");
const toggle = (page: Page) => page.locator("[data-assistant-toggle]");
const composer = (page: Page) => panel(page).getByRole("textbox", { name: /^Message / });

/** Tag the chat's first element. A remount would draw a new one, with no tag. */
async function markChat(page: Page) {
  await page.evaluate(() => {
    const el = document.querySelector("[data-assistant-chat]")?.firstElementChild as HTMLElement | null;
    if (!el) throw new Error("no chat to mark");
    el.dataset.e2eMark = "kept";
  });
}
async function chatKept(page: Page): Promise<boolean> {
  return page.evaluate(
    () => (document.querySelector("[data-assistant-chat]")?.firstElementChild as HTMLElement | null)?.dataset.e2eMark === "kept",
  );
}

async function openHome(page: Page, width: number, height = 900, opts: { chatFirst?: boolean; pill?: boolean } = {}) {
  await page.setViewportSize({ width, height });
  await install(page, opts);
  await page.goto("/");
}

/** The phone's Ask tab opens the sheet, with the composer ready. */
async function openSheet(page: Page) {
  const ask = page.locator("[data-ask-tab]");
  await expect(ask).toBeVisible({ timeout: 60_000 });
  await ask.click();
  await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

/**
 * The strip shows: the same section, with a 56 px row on top of the bottom
 * nav. The section runs under the nav to the screen's foot, so no gap shows
 * between the row and the nav. The row rises in, so the boxes are polled.
 */
async function expectStrip(page: Page, height: number) {
  await expect(panel(page)).toHaveAttribute("data-panel-form", "peek");
  await expect(panel(page)).toHaveAttribute("data-assistant", "peek");
  await expect(panel(page)).toBeVisible();
  const row = panel(page).locator("[data-assistant-strip]");
  await expect(row).toBeVisible();
  const nav = page.locator("[data-ask-tab]").locator("xpath=ancestor::div[contains(@class,'fixed')][1]");
  await expect
    .poll(async () => {
      const r = await row.boundingBox();
      const n = await nav.boundingBox();
      const p = await panel(page).boundingBox();
      if (!r || !n || !p) return "missing";
      return {
        rowHeight: Math.round(r.height),
        // The row ends at or above the nav's top edge, within the nav's clearance.
        rowOnNav: r.y + r.height <= n.y + 1 && r.y + r.height >= n.y - 16,
        // The section reaches the foot of the screen, under the nav.
        reachesFoot: Math.round(p.y + p.height) === height,
      };
    })
    .toEqual({ rowHeight: 56, rowOnNav: true, reachesFoot: true });
  // The page's last row moves up by the strip: the nav's 3.5rem, and 3.5rem more.
  await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-assistant-peek"))).toBe(true);
  await expect
    .poll(() => page.evaluate(() => getComputedStyle(document.querySelector("main.pb-nav")!).paddingBottom))
    .toBe("112px");
  // The sheet's header is hidden and inert under the strip.
  await expect(panel(page).getByRole("button", { name: "Show as a strip" })).toHaveCount(0);
}

async function openPanel(page: Page) {
  await expect(toggle(page)).toBeVisible({ timeout: 60_000 });
  await toggle(page).click();
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

test.describe("the assistant opens, closes and goes full screen as one element", () => {
  test.setTimeout(120_000);

  test("a live run survives a close, a reopen, the full view and the way back", async ({ page }) => {
    await openHome(page, 1440);
    await openPanel(page);
    await expect(panel(page)).toHaveAttribute("data-assistant", "dock");
    await expect(panel(page)).toHaveAttribute("data-panel-form", "dock");
    // The page gives up the panel's width, as it does for the sidebar.
    await expect.poll(async () => Math.round((await spacer(page).boundingBox())?.width ?? -1)).toBe(400);
    await expect(composer(page)).toBeFocused();

    await composer(page).fill("Plan the release");
    await panel(page).getByRole("button", { name: "Send", exact: true }).click();
    await expect(panel(page).getByText("Working on the plan.").first()).toBeVisible({ timeout: 15_000 });
    await markChat(page);

    // Close: the panel stays mounted, hidden and inert, and focus goes back.
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toBeHidden();
    await expect.poll(async () => Math.round((await spacer(page).boundingBox())?.width ?? -1)).toBe(0);
    await expect(toggle(page)).toBeFocused();
    expect(await chatKept(page)).toBe(true);

    // Reopen: the same chat, with the run still going.
    await toggle(page).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
    await expect(panel(page).getByText("Working on the plan.").first()).toBeVisible();
    expect(await chatKept(page)).toBe(true);

    // The full view, and back. The page behind keeps its width.
    await panel(page).getByRole("button", { name: "Show full screen" }).click();
    await expect(panel(page)).toHaveAttribute("data-assistant", "full");
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
    await expect(panel(page).getByText("Working on the plan.").first()).toBeVisible();
    await expect.poll(async () => Math.round((await spacer(page).boundingBox())?.width ?? -1)).toBe(400);
    expect(await chatKept(page)).toBe(true);
    // The view transition is over: nothing is left on <html> or the panel.
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("panel-swap"))).toBe(false);

    await panel(page).getByRole("button", { name: "Show as a side panel" }).click();
    await expect(panel(page)).toHaveAttribute("data-assistant", "dock");
    await expect(panel(page).getByText("Working on the plan.").first()).toBeVisible();
    expect(await chatKept(page)).toBe(true);

    // The held stream ends in the same chat.
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    await expect(panel(page).getByText("The due date is Friday.").first()).toBeVisible({ timeout: 15_000 });
    expect(await chatKept(page)).toBe(true);
  });

  test("reduced motion lands in the same states", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openHome(page, 1440);
    await openPanel(page);
    await panel(page).getByRole("button", { name: "Show full screen" }).click();
    await expect(panel(page)).toHaveAttribute("data-assistant", "full");
    await panel(page).getByRole("button", { name: "Show as a side panel" }).click();
    await expect(panel(page)).toHaveAttribute("data-assistant", "dock");
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(toggle(page)).toBeFocused();
  });

  test("a narrow desktop floats the panel over the page, with no spacer", async ({ page }) => {
    await openHome(page, 1100);
    await openPanel(page);
    await expect(panel(page)).toHaveAttribute("data-panel-form", "dock");
    await expect(spacer(page)).toHaveCount(0);
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toBeHidden();
  });

  test("a phone opens a sheet from the Ask tab, and a close gives the tab its focus back", async ({ page }) => {
    await openHome(page, 390, 844);
    const ask = page.locator("[data-ask-tab]");
    await openSheet(page);
    // Flag off (CF-1): the sheet has no way down, and no handle.
    await expect(panel(page).getByRole("button", { name: "Show as a strip" })).toHaveCount(0);
    await expect(panel(page).locator("[data-assistant-handle]")).toHaveCount(0);
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toBeHidden();
    await expect(ask).toBeFocused();
  });

  test("a phone drops the sheet to the peek strip, and a tap opens the same chat (CF-1)", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true });
    await openSheet(page);
    await composer(page).fill("a draft I keep");
    await markChat(page);

    // The handle is there, and the chevron drops the sheet to the strip.
    await expect(panel(page).locator("[data-assistant-handle]")).toHaveCount(1);
    await panel(page).getByRole("button", { name: "Show as a strip" }).click();
    await expectStrip(page, 844);
    await expect(panel(page).locator("[data-assistant-strip-text]")).toHaveText("Metorite");
    expect(await chatKept(page)).toBe(true);

    // A tap on the strip's line opens the sheet: the same chat, the same draft.
    await panel(page).locator("[data-assistant-strip-text]").click();
    await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
    await expect(composer(page)).toBeEnabled();
    await expect(composer(page)).toHaveValue("a draft I keep");
    expect(await chatKept(page)).toBe(true);
    await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-assistant-peek"))).toBe(false);

    // A drag down on the handle drops it too, and the strip's X closes it.
    const handle = panel(page).locator("[data-assistant-handle]");
    const hb = (await handle.boundingBox())!;
    await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
    await page.mouse.down();
    await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2 + 120, { steps: 6 });
    await page.mouse.up();
    await expectStrip(page, 844);
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-assistant-peek"))).toBe(false);
  });

  test("a pill link in the chat drops the sheet to the strip, and the page shows (CF-1)", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, pill: true });
    await openSheet(page);
    await composer(page).fill("What is next?");
    await panel(page).getByRole("button", { name: "Send", exact: true }).click();
    const pill = panel(page).getByRole("link", { name: /^Open task #171/ });
    await expect(pill).toBeVisible({ timeout: 20_000 });
    await markChat(page);
    await pill.click();
    await expectStrip(page, 844);
    await expect(page).toHaveURL(/\/projects\?task=/, { timeout: 60_000 });
    // The strip says the last reply, as plain words.
    await expect(panel(page).locator("[data-assistant-strip-text]")).toContainText("Next up is #171 Plan the release.");
    expect(await chatKept(page)).toBe(true);
    // The way back up, and the X from the sheet.
    await panel(page).getByRole("button", { name: "Open the chat" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
    await expect(composer(page)).toBeEnabled();
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
  });

  test("with the flag off, a pill link closes the sheet, as before (CF-1)", async ({ page }) => {
    await openHome(page, 390, 844, { pill: true });
    await openSheet(page);
    await composer(page).fill("What is next?");
    await panel(page).getByRole("button", { name: "Send", exact: true }).click();
    const pill = panel(page).getByRole("link", { name: /^Open task #171/ });
    await expect(pill).toBeVisible({ timeout: 20_000 });
    await pill.click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
    await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-assistant-peek"))).toBe(false);
  });

  test("a desktop draws no strip, with the flag on (CF-1, D-CF-1)", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: true });
    await openPanel(page);
    await expect(panel(page)).toHaveAttribute("data-panel-form", "dock");
    await expect(panel(page).getByRole("button", { name: "Show as a strip" })).toHaveCount(0);
    await expect(panel(page).locator("[data-assistant-handle]")).toHaveCount(0);
    await expect(panel(page).locator("[data-assistant-strip]")).toHaveCount(0);
  });
});
