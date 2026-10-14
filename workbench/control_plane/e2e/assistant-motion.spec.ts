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
 *
 * The vitest twin is `src/lib/panelMotion.test.ts`. No gateway runs here:
 * each request the page makes has a stub, or fails at once.
 */

async function install(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.clear();
    // The one assistant needs its flag and the full frame (`chatFlag.ts`).
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
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
  });
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

async function openHome(page: Page, width: number, height = 900) {
  await page.setViewportSize({ width, height });
  await install(page);
  await page.goto("/");
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
    await expect(ask).toBeVisible({ timeout: 60_000 });
    await ask.click();
    await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
    await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
    await panel(page).getByRole("button", { name: "Close Metorite" }).click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "closed");
    await expect(panel(page)).toBeHidden();
    await expect(ask).toBeFocused();
  });
});
