import { expect, test, type Page } from "@playwright/test";

/**
 * WS-62 CF-4 (`chat_first_surface.md` §4.6): with the chat-first flag on,
 * the empty chat of the one assistant shows three pills from the member's
 * My Day feed, in the feed's order, and a tap sends the headline as the
 * member's message. With the feed empty, the fixed list shows. With the flag
 * off, the fixed list shows and the chat never reads the feed.
 *
 * No gateway runs here. `/api/shell/feed` is answered in the page, so the
 * feed is the one this spec writes, and the page counts each read. The
 * vitest twin is `src/lib/starterPills.test.ts`, and the source block in
 * `src/lib/shell/assistant.test.ts` pins the wiring.
 */

interface StubCard {
  signal_id: string;
  app: string;
  kind: string;
  group: string;
  title: string;
  href: string;
}

/** Three cards, in the feed's order: Now first, then Needs you. */
const THREE: StubCard[] = [
  { signal_id: "tasks:due-today", app: "tasks", kind: "due_today", group: "now", title: "3 tasks are due today", href: "/tasks" },
  { signal_id: "email:reply-priya", app: "email", kind: "needs_reply", group: "needs_you", title: "Reply to Priya Nair", href: "/email" },
  { signal_id: "crm:acme-quiet", app: "crm", kind: "deal_quiet", group: "needs_you", title: "The Acme deal has not moved in 9 days", href: "/crm" },
];

/** The one assistant's fixed list (`AgentChat.tsx`, `AGENT_SUGGESTIONS.orchestrator`). */
const FIXED = ["What can you help me with?", "Summarize recent activity across the company", "Create a task in Projects"];

interface InstallOptions {
  chatFirst?: boolean;
  /** The cards the feed answers with. */
  cards: StubCard[];
}

async function install(page: Page, opts: InstallOptions) {
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
/** The starter pills: the buttons under the "Try asking" label of the empty state. */
const starters = (page: Page) =>
  panel(page).getByText("Try asking", { exact: true }).locator("xpath=following-sibling::div[1]//button");

const chatPosts = (page: Page) =>
  page.evaluate(() => (window as unknown as { __chatPosts: Array<{ message?: string }> }).__chatPosts.map((p) => p.message));
const feedGets = (page: Page) => page.evaluate(() => (window as unknown as { __feedGets: number }).__feedGets);

async function openHome(page: Page, width: number, height: number, opts: InstallOptions) {
  await page.setViewportSize({ width, height });
  await install(page, opts);
  await page.goto("/");
}

async function openPanel(page: Page) {
  const toggle = page.locator("[data-assistant-toggle]");
  await expect(toggle).toBeVisible({ timeout: 60_000 });
  await toggle.click();
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

async function openSheet(page: Page) {
  const ask = page.locator("[data-ask-tab]");
  await expect(ask).toBeVisible({ timeout: 60_000 });
  await ask.click();
  await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet");
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

/** The three pills are the feed's headlines, in the feed's order, and a tap sends the first. */
async function expectFeedPillsAndTap(page: Page) {
  await expect(starters(page)).toHaveText(THREE.map((c) => c.title), { timeout: 30_000 });
  expect(await feedGets(page)).toBeGreaterThan(0);
  await panel(page).getByRole("button", { name: THREE[0].title, exact: true }).click();
  // The headline went as the member's message, word for word, and the reply came.
  await expect.poll(() => chatPosts(page), { timeout: 15_000 }).toEqual([THREE[0].title]);
  await expect(panel(page).getByText("Here is your day.").first()).toBeVisible({ timeout: 15_000 });
  // The empty state is gone with its pills.
  await expect(panel(page).getByText("Try asking", { exact: true })).toHaveCount(0);
}

test.describe("the starter pills come from the member's feed (WS-62 CF-4)", () => {
  test.setTimeout(120_000);

  test("a desktop with the flag on shows the three headlines, and a tap sends the first", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: true, cards: THREE });
    await openPanel(page);
    await expectFeedPillsAndTap(page);
  });

  test("a phone with the flag on shows the three headlines in the sheet, and a tap sends the first", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, cards: THREE });
    await openSheet(page);
    await expectFeedPillsAndTap(page);
  });

  test("an empty feed shows the fixed list, with the flag on", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: true, cards: [] });
    await openPanel(page);
    await expect(starters(page)).toHaveText(FIXED, { timeout: 30_000 });
    // The feed was read, and it gave no card.
    await expect.poll(() => feedGets(page)).toBeGreaterThan(0);
    await page.waitForTimeout(300);
    await expect(starters(page)).toHaveText(FIXED);
  });

  test("with the flag off, the fixed list shows and the chat never reads the feed", async ({ page }) => {
    await openHome(page, 1440, 900, { chatFirst: false, cards: THREE });
    await openPanel(page);
    await expect(starters(page)).toHaveText(FIXED, { timeout: 30_000 });
    await page.waitForTimeout(500);
    await expect(starters(page)).toHaveText(FIXED);
    expect(await feedGets(page)).toBe(0);
  });
});
