import { expect, test, type Page } from "@playwright/test";

/**
 * WS-63 PZ-27 (`personalization.md` §14.4): "Yes, start" on the Get to know
 * me offer sends the request as the member's turn in a fresh chat.
 *
 * The owner pressed it on production on 2026-10-26 and nothing happened.
 * The offer is drawn inside the open assistant. Its fresh open with words
 * changed the chat in one dock effect, and the next effect decided the words
 * for the OLD chat in the same pass. The new chat then never got them
 * (`useAssistantChat` in `src/lib/shell/AssistantDock.tsx`). The source pin
 * is in `src/lib/shell/assistant.test.ts`.
 *
 * No gateway runs here. The page answers each fetch, and it answers the
 * profile with the interview not started, so the offer shows.
 */

interface StubCard { signal_id: string; app: string; kind: string; group: string; title: string; href: string }

async function install(page: Page, opts: { chatFirst?: boolean; cards: StubCard[] }) {
  await page.addInitScript(({ chatFirst, cards }) => {
    window.localStorage.clear();
    // The one assistant needs its flag and the full frame (`chatFlag.ts`).
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
    if (chatFirst) window.localStorage.setItem("cc-shell-chat-first", "1");
    const w = window as unknown as Record<string, unknown>;
    // Each body the chat POSTed, and each read of the feed. A fetch answered
    // inside the page never reaches Playwright's network events.
    const posts: Array<Record<string, unknown>> = [];
    w.__chatPosts = posts;
    w.__feedGets = 0;
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/shell/feed")) {
        w.__feedGets = (w.__feedGets as number) + 1;
        const full = cards.map((c) => ({ ...c, folded: [], score: 1, why: null, at: null, acts: [] }));
        const ids = [...new Set(full.map((c) => c.group))];
        const groups = ids.map((id) => ({ id, count: full.filter((c) => c.group === id).length }));
        return json({
          cards: full, groups, handled: [], handled_ok: true, needs: null,
          sources: { tasks: "ok", email: "ok", crm: "ok" }, capped: [],
        });
      }
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (url.includes("/api/auth/me/assistant-profile")) {
        w.__profileHits = ((w.__profileHits as number) ?? 0) + 1;
        if ((init?.method ?? "GET").toUpperCase() !== "GET") return json({ ok: true });
        return json({ enabled: true, learning: true, facts: [], address: {}, style: {}, aliases: [] });
      }
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
      if (url.includes("/api/chat/suggestions")) return json({ suggestions: [] });
      if (url.includes("/api/chat/sessions")) {
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("/room")) return json({ detail: "not a room" }, 404);
        return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        if (body.reconnect) return json({}, 404);
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        posts.push(body);
        const done = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            c.enqueue(frame({ type: "delta", content: "Here is your day.", messageId: "m-1" }));
            c.enqueue(frame({ type: "message_end", messageId: "m-1" }));
            c.enqueue(frame({ type: "done" }));
            c.close();
          },
        });
        return new Response(done, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { chatFirst: Boolean(opts.chatFirst), cards: opts.cards });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

const panel = (page: Page) => page.locator("section[data-assistant]");
const composer = (page: Page) => panel(page).getByRole("textbox", { name: /^Message / });
const offer = (page: Page) => panel(page).getByRole("region", { name: "Get to know me" });
const chatPosts = (page: Page) =>
  page.evaluate(() => (window as unknown as { __chatPosts: Array<{ message?: string }> }).__chatPosts.map((p) => p.message));

const MESSAGE = "Let's do the Get to know me interview.";

async function pressYes(page: Page) {
  await expect(offer(page)).toBeVisible({ timeout: 60_000 });
  await offer(page).getByRole("button", { name: "Yes, start" }).click();
  // The words went as the member's turn, once, and the empty state is gone.
  await expect.poll(() => chatPosts(page), { timeout: 15_000 }).toEqual([MESSAGE]);
  await expect(offer(page)).toHaveCount(0);
  await expect(panel(page).getByText("Here is your day.").first()).toBeVisible({ timeout: 15_000 });
}

test.describe("Yes, start on the Get to know me offer sends the request (PZ-27)", () => {
  test.setTimeout(120_000);

  test("the side panel, opened from the home page", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await install(page, { cards: [] });
    await page.goto("/");
    const toggle = page.locator("[data-assistant-toggle]");
    await expect(toggle).toBeVisible({ timeout: 60_000 });
    await expect(async () => {
      await toggle.click();
      await expect(panel(page)).toHaveAttribute("data-panel-phase", "open", { timeout: 5_000 });
    }).toPass({ timeout: 60_000 });
    await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
    await pressYes(page);
  });

  test("the full view at /chat", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await install(page, { cards: [] });
    await page.goto("/chat");
    await expect(composer(page)).toBeEnabled({ timeout: 60_000 });
    await pressYes(page);
  });

  test("the full view at /chat on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await install(page, { cards: [] });
    await page.goto("/chat");
    await expect(composer(page)).toBeEnabled({ timeout: 60_000 });
    await pressYes(page);
  });

  test("No thanks hides the offer and sends nothing to the chat", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await install(page, { cards: [] });
    await page.goto("/chat");
    await expect(offer(page)).toBeVisible({ timeout: 60_000 });
    await offer(page).getByRole("button", { name: "No thanks" }).click();
    await expect(offer(page)).toHaveCount(0);
    expect(await chatPosts(page)).toEqual([]);
  });
});
