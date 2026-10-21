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
 * - With the flag on (CF-2), a phone that lands on `/` opens the sheet, once
 *   per browser session and never on a deep link. A member who never wrote
 *   sees one nudge: under the field on a desktop, above the Ask tab on a
 *   phone. The first message and the X retire it, on the server.
 * - With the flag on (CF-8), the sheet has one header row above the
 *   messages, no Share button, no model picker, and Send. The "…" control
 *   at the composer's end holds Share and the effort choice. With the flag
 *   off, the agent row and the Share row still show.
 *
 * The vitest twin is `src/lib/panelMotion.test.ts`. No gateway runs here:
 * each request the page makes has a stub, or fails at once.
 */

/** A task the stubbed answer names, so the chat draws a task pill (`ChatTaskRef`). */
const TASK_ID = "00000000-0000-4000-8000-000000000171";

/** A member's shell layout, as `/api/auth/me/shell` answers it (NS-7, CF-2). */
interface ShellLayout {
  preset: string | null;
  answered: "answered" | "skipped" | null;
  pins: string[] | null;
  known: string[] | null;
  cardOrder: string[] | null;
  newOrder: string[] | null;
  chat_first_message_at: string | null;
}

/** A member who skipped the question and never wrote to the assistant. */
const NEVER_WROTE: ShellLayout = {
  preset: null, answered: "skipped", pins: null, known: null, cardOrder: null, newOrder: null, chat_first_message_at: null,
};
/** A member who wrote once, on some device. */
const WROTE: ShellLayout = { ...NEVER_WROTE, chat_first_message_at: "2026-10-20T09:00:00.000Z" };
/** A member the server never asked: the welcome dialog opens. */
const NEVER_ASKED: ShellLayout = { ...NEVER_WROTE, answered: null };

interface InstallOptions {
  chatFirst?: boolean;
  pill?: boolean;
  /**
   * Serve `/api/auth/me/shell` from an in-page store that starts here. A PUT
   * merges into it and `window.__shellPuts` keeps each body. Without it the
   * read is refused, as every other request without a stub is.
   */
  shell?: ShellLayout;
  /** Hold `/api/auth/me` and the layout read this long, so the shell lands before they answer. */
  delayMs?: number;
}

async function install(page: Page, opts: InstallOptions = {}) {
  await page.addInitScript(({ chatFirst, pill, taskId, shell, delayMs }) => {
    window.localStorage.clear();
    // The one assistant needs its flag and the full frame (`chatFlag.ts`).
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
    // CF-1: the chat-first flag, through its dev override. Off unless asked.
    if (chatFirst) window.localStorage.setItem("cc-shell-chat-first", "1");
    const w = window as unknown as Record<string, unknown>;
    let open: (() => void) | null = null;
    w.__release = () => open?.();
    // CF-2: the layout store. It lives in `sessionStorage`, so a reload reads
    // what the page wrote, as the gateway would answer.
    const SHELL_KEY = "e2e-shell-layout";
    const puts: unknown[] = [];
    w.__shellPuts = puts;
    // How many reads answered. A spec polls it: a fetch answered inside the
    // page never reaches Playwright's network events.
    w.__shellGets = 0;
    if (shell && !sessionStorage.getItem(SHELL_KEY)) sessionStorage.setItem(SHELL_KEY, JSON.stringify(shell));
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (delayMs && (url.includes("/api/auth/me/shell") || url.endsWith("/api/auth/me"))) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
      // Before `/api/auth/me`, which this path also contains.
      if (shell && url.includes("/api/auth/me/shell")) {
        const stored = JSON.parse(sessionStorage.getItem(SHELL_KEY) ?? "null") as ShellLayout | null;
        if ((init?.method ?? "GET").toUpperCase() === "PUT") {
          const body = JSON.parse(String(init?.body ?? "null")) as Partial<ShellLayout> | null;
          puts.push(body);
          // As the gateway does: a key the body omits keeps its stored value.
          const next = body === null ? shell : { ...(stored ?? shell), ...body };
          sessionStorage.setItem(SHELL_KEY, JSON.stringify(next));
          return json(next);
        }
        w.__shellGets = (w.__shellGets as number) + 1;
        return json(stored ?? shell);
      }
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
  }, { chatFirst: Boolean(opts.chatFirst), pill: Boolean(opts.pill), taskId: TASK_ID, shell: opts.shell ?? null, delayMs: opts.delayMs ?? 0 });
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

async function openHome(page: Page, width: number, height = 900, opts: InstallOptions = {}, path = "/") {
  await page.setViewportSize({ width, height });
  await install(page, opts);
  await page.goto(path);
}

/** The bodies the page PUT to `/api/auth/me/shell`, in order. */
const shellPuts = (page: Page) =>
  page.evaluate(() => (window as unknown as { __shellPuts: Array<Partial<ShellLayout> | null> }).__shellPuts);

/**
 * The layout read has answered in THIS page, so the landing rule and the
 * nudge have had their facts. Then a beat, so an open that was going to
 * happen has happened.
 */
async function shellRead(page: Page) {
  await expect.poll(() => page.evaluate(() => (window as unknown as { __shellGets: number }).__shellGets), { timeout: 60_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(500);
}

/** The one nudge, by its placement. */
const nudge = (page: Page, placement: "field" | "tab") => page.locator(`[data-chat-nudge="${placement}"]`);

/**
 * No open sheet for this long. The dock mounts lazily, so a count of zero at
 * one instant proves nothing: this samples until the time is up.
 */
async function expectNoOpenSheetFor(page: Page, ms: number) {
  const open = page.locator('section[data-assistant][data-panel-phase="open"]');
  const until = Date.now() + ms;
  while (Date.now() < until) {
    expect(await open.count(), "the sheet opened").toBe(0);
    await page.waitForTimeout(100);
  }
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
    // Flag off (CF-8): the agent row and the Share row still show, and the
    // chat has no "…" control.
    await expect(panel(page).locator("[data-chat-agent-row]")).toBeVisible();
    await expect(panel(page).locator("[data-chat-room-row]")).toBeVisible();
    await expect(panel(page).getByRole("button", { name: "Share" })).toBeVisible();
    await expect(panel(page).locator("[data-chat-more]")).toHaveCount(0);
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

  test("the phone sheet shows one header row, one field and one send, with the flag on (CF-8)", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true });
    await openSheet(page);
    const sheet = panel(page);

    // One header row above the messages: the dock's. The chat's agent row
    // and its Share row are gone.
    await expect(sheet.locator("header")).toHaveCount(1);
    await expect(sheet.locator("[data-chat-agent-row]")).toHaveCount(0);
    await expect(sheet.locator("[data-chat-room-row]")).toHaveCount(0);
    await expect(sheet.getByRole("button", { name: "Share" })).toHaveCount(0);
    // The row's five controls, at the phone's tap size, and no Files.
    for (const name of ["Your chats", "New chat", "Show as a strip", "Close Metorite"]) {
      const b = sheet.getByRole("button", { name, exact: true });
      await expect(b).toBeVisible();
      const box = (await b.boundingBox())!;
      expect(Math.round(box.height), name).toBeGreaterThanOrEqual(44);
      expect(Math.round(box.width), name).toBeGreaterThanOrEqual(44);
    }
    await expect(sheet.getByRole("button", { name: "Open this chat's files in Artifacts" })).toHaveCount(0);

    // One field, one send: no model picker, no effort picker in the box.
    await expect(sheet.locator("[data-chat-pickers]")).toHaveCount(0);
    await expect(sheet.locator("[data-chat-composer-ring]")).toHaveCount(0);
    const send = sheet.getByRole("button", { name: "Send", exact: true });
    await expect(send).toBeVisible();
    const sendBox = (await send.boundingBox())!;
    expect(Math.round(sendBox.height)).toBeGreaterThanOrEqual(44);

    // The "…" control holds Share, the files and the effort choice.
    const more = sheet.locator("[data-chat-more]");
    await expect(more).toBeVisible();
    await more.click();
    const menu = page.getByRole("menu", { name: "More" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Share this conversation" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "This chat's files" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /^Auto/ })).toBeVisible();
    await expect(menu.locator("[data-chat-more-context]")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);

    // The reading width: the thread's gutter is 16 px each side at 390.
    const thread = sheet.locator("[data-assistant-chat] .overflow-y-auto").first();
    await expect
      .poll(() => thread.evaluate((el) => [getComputedStyle(el).paddingLeft, getComputedStyle(el).paddingRight]))
      .toEqual(["16px", "16px"]);
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
    // The strip says the last reply, as plain words, at once. The run just
    // ended, and the status row's "Done" never reaches the strip.
    await expect(panel(page).locator("[data-assistant-strip-text]")).toHaveText("Next up is #171 Plan the release.", {
      timeout: 2_000,
    });
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

/**
 * The sheet opened on its own: the section is there, open, as the sheet,
 * and nobody tapped the Ask tab.
 */
async function expectLandedOnChat(page: Page) {
  await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet", { timeout: 60_000 });
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

/** Nothing opened: the Ask tab is there, and the dock was never mounted. */
async function expectNoLanding(page: Page) {
  await expect(page.locator("[data-ask-tab]")).toBeVisible({ timeout: 60_000 });
  await shellRead(page);
  await expect(panel(page)).toHaveCount(0);
}

test.describe("the phone opens on the chat, and one nudge (WS-62 CF-2)", () => {
  test.setTimeout(120_000);

  test("a phone with the flag on lands on the sheet once per session, and never on a deep link", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, shell: WROTE });
    await expectLandedOnChat(page);
    expect(await page.evaluate(() => sessionStorage.getItem("cc-shell-chat-landed"))).toBe("1");

    // A reload in the same tab: the member saw it, so it stays shut.
    await page.reload();
    await expectNoLanding(page);

    // A new session that lands on a deep link: the page shows, the chat waits.
    await page.evaluate(() => sessionStorage.removeItem("cc-shell-chat-landed"));
    await page.goto("/?from=link");
    await expectNoLanding(page);
    expect(await page.evaluate(() => sessionStorage.getItem("cc-shell-chat-landed"))).toBeNull();
  });

  test("the flag off, a phone lands on the page, as before", async ({ page }) => {
    await openHome(page, 390, 844, { shell: WROTE });
    await expectNoLanding(page);
    await expect(nudge(page, "tab")).toHaveCount(0);
  });

  test("a desktop lands on the page, with the flag on (D-CF-2)", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: true, shell: WROTE });
    await expect(toggle(page)).toBeVisible({ timeout: 60_000 });
    await shellRead(page);
    await expect(panel(page)).toHaveCount(0);
    await expect(nudge(page, "field")).toHaveCount(0);
  });

  test("a phone shows the nudge above the Ask tab, and the first message retires it", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, shell: NEVER_WROTE });
    await expectLandedOnChat(page);
    // Under the sheet the bubble waits. The X on the sheet shows it.
    await expect(nudge(page, "tab")).toHaveCount(0);
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    const bubble = nudge(page, "tab");
    await expect(bubble).toBeVisible();
    await expect(bubble).toHaveText(/Start here\./);
    await expect(bubble.getByRole("button", { name: "Dismiss" })).toBeVisible();
    // Above the Ask tab, and the page's last row moved up for it.
    const ask = page.locator("[data-ask-tab]");
    const b = (await bubble.boundingBox())!;
    const a = (await ask.boundingBox())!;
    expect(b.y + b.height).toBeLessThanOrEqual(a.y + 1);
    expect(b.x + b.width).toBeGreaterThan(a.x);
    await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-chat-nudge"))).toBe(true);
    await expect
      .poll(() => page.evaluate(() => getComputedStyle(document.querySelector("main.pb-nav")!).paddingBottom))
      .toBe("104px");

    // The first message. The sheet covers the bubble while it is open.
    await openSheet(page);
    await expect(nudge(page, "tab")).toHaveCount(0);
    await composer(page).fill("Plan the release");
    await panel(page).getByRole("button", { name: "Send", exact: true }).click();
    await expect(panel(page).getByText("Working on the plan.").first()).toBeVisible({ timeout: 15_000 });
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    await expect(panel(page).getByText("The due date is Friday.").first()).toBeVisible({ timeout: 15_000 });
    await expect.poll(async () => (await shellPuts(page)).length, { timeout: 15_000 }).toBe(1);
    const [put] = await shellPuts(page);
    expect(Number.isFinite(Date.parse(put?.chat_first_message_at ?? ""))).toBe(true);
    // The key alone: the server merges it, so no star of another device is written over.
    expect(Object.keys(put ?? {})).toEqual(["chat_first_message_at"]);

    // Closed again: no bubble, and the page's last row is back.
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(nudge(page, "tab")).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.hasAttribute("data-chat-nudge"))).toBe(false);

    // Another visit reads the server's mark, and shows nothing.
    await page.reload();
    await expectNoLanding(page);
    await expect(nudge(page, "tab")).toHaveCount(0);
  });

  test("a desktop shows the nudge under the field, and the X retires it for every device", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: true, shell: NEVER_WROTE });
    const bubble = nudge(page, "field");
    await expect(bubble).toBeVisible({ timeout: 60_000 });
    await expect(bubble).toHaveText(/Ask me anything\. I can read your mail, plan your week and move your tasks\./);
    // Under the field, centred on it, with the tail up.
    const field = page.locator("[data-ask-field] input");
    const b = (await bubble.boundingBox())!;
    const f = (await field.boundingBox())!;
    expect(b.y).toBeGreaterThanOrEqual(f.y + f.height);
    expect(Math.abs(b.x + b.width / 2 - (f.x + f.width / 2))).toBeLessThan(f.width / 2);
    // No sheet, no auto-open: the page is the member's (D-CF-2).
    await expect(panel(page)).toHaveCount(0);

    await bubble.getByRole("button", { name: "Dismiss" }).click();
    await expect(nudge(page, "field")).toHaveCount(0);
    await expect.poll(async () => (await shellPuts(page)).length, { timeout: 15_000 }).toBe(1);
    const [put] = await shellPuts(page);
    expect(Number.isFinite(Date.parse(put?.chat_first_message_at ?? ""))).toBe(true);

    // "Another browser": the copy is gone, and the server says the member wrote.
    await page.evaluate(() => {
      for (const k of Object.keys(localStorage)) if (k.startsWith("cc-shell-chat-nudge:")) localStorage.removeItem(k);
    });
    await page.reload();
    await expect(toggle(page)).toBeVisible({ timeout: 60_000 });
    await shellRead(page);
    await expect(nudge(page, "field")).toHaveCount(0);
  });

  test("on a phone, the welcome question comes first, has no step 2, and the sheet opens after it", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, shell: NEVER_ASKED });
    const question = page.getByRole("dialog", { name: "What will you do most here?" });
    await expect(question).toBeVisible({ timeout: 60_000 });
    // While it is open: no sheet, and no nudge. Sampled, because the dock
    // mounts lazily and a count of zero at one instant proves nothing.
    await expectNoOpenSheetFor(page, 1_500);
    await expect(nudge(page, "tab")).toHaveCount(0);

    // Step 1's answer writes the preset and its stars, and there is no step 2.
    await question.getByRole("button", { name: /Build and ship the work/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Choose the apps for your sidebar" })).toHaveCount(0);
    await expect.poll(async () => (await shellPuts(page)).length).toBe(1);
    const [put] = await shellPuts(page);
    expect(put?.preset).toBe("engineer");
    expect(put?.answered).toBe("answered");
    expect(Array.isArray(put?.pins)).toBe(true);
    expect(Array.isArray(put?.known)).toBe(true);
    // The body names no mark: the server keeps whatever it holds.
    expect(put && "chat_first_message_at" in put ? put.chat_first_message_at : null).toBeNull();

    // The dialog shut, so the landing open runs now.
    await expectLandedOnChat(page);
  });

  test("a slow access read and a slow layout read: the question still comes before the sheet", async ({ page }) => {
    // The shell lands at once, and the reads answer 2 s later. The landing
    // must wait for both, or the question opens on top of the open sheet
    // (review of #1206).
    await openHome(page, 390, 844, { chatFirst: true, shell: NEVER_ASKED, delayMs: 2_000 });
    const question = page.getByRole("dialog", { name: "What will you do most here?" });
    await expect(question).toBeVisible({ timeout: 60_000 });
    await expectNoOpenSheetFor(page, 1_500);
    expect(await page.evaluate(() => sessionStorage.getItem("cc-shell-chat-landed"))).toBeNull();
    await question.getByRole("button", { name: /Build and ship the work/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expectLandedOnChat(page);
  });
});
