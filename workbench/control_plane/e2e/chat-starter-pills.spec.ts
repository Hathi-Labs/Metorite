import { expect, test, type Page } from "@playwright/test";

/**
 * WS-62 CF-4b (`chat_first_surface.md` §4.6, D-CF-8): with the chat-first
 * flag on, a fresh chat of the one assistant shows three action pills from
 * the member's My Day feed, in the feed's order. A card of the groups fyi,
 * later and handled never fills a slot. A tap sends a message that names the
 * item. A phone chat that holds only the day's brief shows the pills under
 * it, and a chat with a member message shows none. With the feed empty, the
 * fixed list shows. With the flag off, the fixed list shows and the chat
 * never reads the feed.
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
  { signal_id: "tasks:due-today", app: "tasks", kind: "due_today", group: "now", title: "Send the signed contract", href: "/tasks" },
  { signal_id: "email:reply-priya", app: "email", kind: "needs_reply", group: "needs_you", title: "Q3 budget", href: "/email" },
  { signal_id: "crm:acme-quiet", app: "crm", kind: "deal_quiet", group: "needs_you", title: "Acme renewal", href: "/crm" },
];

/** What the three cards above become: the label and the message of each pill. */
const LABELS = [
  "Finish “Send the signed contract” today",
  "Draft a reply to “Q3 budget”",
  "Revive the “Acme renewal” deal",
];
const FIRST_MESSAGE =
  'Help me finish the task "Send the signed contract" that is due today. What should I do first?';

/** The one assistant's fixed list (`AgentChat.tsx`, `AGENT_SUGGESTIONS.orchestrator`). */
const FIXED = ["What can you help me with?", "Summarize recent activity across the company", "Create a task in Projects"];

interface InstallOptions {
  chatFirst?: boolean;
  /** The cards the feed answers with. */
  cards: StubCard[];
  /**
   * The saved chat holds one assistant row. A `brief` row has the id the
   * gateway writes for the day's brief (CF-5). A `reminder` row is the first
   * message of a chat that a reminder opened (RM-4), with another id.
   */
  first?: "brief" | "reminder";
}

async function install(page: Page, opts: InstallOptions) {
  await page.addInitScript(({ chatFirst, cards, first }) => {
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
        if (first && method === "GET" && /\/messages(\?|$)/.test(url)) {
          return json([{
            id: first === "brief" ? "brief-2026-11-03" : "rem-7f3a",
            role: "assistant",
            content: first === "brief" ? "Your day: three things need you." : "Reminder: call the printer vendor.",
            timestamp: Date.now(),
          }]);
        }
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
  }, { chatFirst: Boolean(opts.chatFirst), cards: opts.cards, first: opts.first });
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
  // The top-bar toggle is gone (GL-12, 30e72d332). The Ask bar's side-panel
  // button opens the dock.
  const toggle = page.getByRole("button", { name: "Open the chat beside the page" });
  await expect(toggle).toBeVisible({ timeout: 60_000 });
  // A press before the bar hydrates does nothing, so press again while the
  // button is still there. It leaves the bar once the panel is asked for, so
  // a press that worked is never repeated. The panel's chunk loads on demand.
  await expect(async () => {
    if (await toggle.isVisible()) await toggle.click();
    await expect(panel(page)).toHaveAttribute("data-panel-phase", "open", { timeout: 4_000 });
  }).toPass({ timeout: 60_000 });
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

async function openSheet(page: Page) {
  const ask = page.locator("[data-ask-tab]");
  await expect(ask).toBeVisible({ timeout: 60_000 });
  // MOB-9: Ask opens AND closes the sheet, and a chat-first phone opens the
  // sheet by itself on landing (CF-2). So tap only while it is not open, and
  // try again: the landing open can arrive between the check and the tap, and
  // a tap before the bar hydrates does nothing.
  await expect(async () => {
    if ((await ask.getAttribute("aria-pressed")) !== "true") await ask.click();
    await expect(panel(page)).toHaveAttribute("data-panel-form", "sheet", { timeout: 4_000 });
  }).toPass({ timeout: 60_000 });
  await expect(panel(page)).toHaveAttribute("data-panel-phase", "open");
  await expect(composer(page)).toBeEnabled({ timeout: 30_000 });
}

/** The three pills are action prompts, in the feed's order, and a tap sends the first message. */
async function expectFeedPillsAndTap(page: Page) {
  await expect(starters(page)).toHaveText(LABELS, { timeout: 30_000 });
  expect(await feedGets(page)).toBeGreaterThan(0);
  await panel(page).getByRole("button", { name: LABELS[0], exact: true }).click();
  // The message went as the member's message, and the reply came.
  await expect.poll(() => chatPosts(page), { timeout: 15_000 }).toEqual([FIRST_MESSAGE]);
  await expect(panel(page).getByText("Here is your day.").first()).toBeVisible({ timeout: 15_000 });
  // The empty state is gone with its pills.
  await expect(panel(page).getByText("Try asking", { exact: true })).toHaveCount(0);
}

test.describe("the starter pills are action prompts from the member's feed (WS-62 CF-4b)", () => {
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

  test("a card of the fyi, later and handled groups never fills a slot", async ({ page }) => {
    const quiet = ["fyi", "later", "handled"].map((group, i) => ({
      signal_id: `tasks:quiet-${i}`, app: "tasks", kind: "overdue", group, title: `Quiet ${i}`, href: "/tasks",
    }));
    await openHome(page, 1440, 900, { chatFirst: true, cards: [...quiet, ...THREE.slice(0, 1)] });
    await openPanel(page);
    await expect(starters(page)).toHaveText(LABELS.slice(0, 1), { timeout: 30_000 });
  });

  test("a phone chat that holds only the brief shows the pills under it", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, cards: THREE, first: "brief" });
    await openSheet(page);
    await expect(panel(page).getByText("Your day: three things need you.")).toBeVisible({ timeout: 30_000 });
    await expect(starters(page)).toHaveText(LABELS, { timeout: 30_000 });
    // A tap sends, and then no pill shows: the member has spoken.
    await panel(page).getByRole("button", { name: LABELS[1], exact: true }).click();
    await expect.poll(() => chatPosts(page), { timeout: 15_000 }).toHaveLength(1);
    await expect(panel(page).getByText("Try asking", { exact: true })).toHaveCount(0);
  });

  test("a phone chat that a reminder opened shows no starter pill and keeps its follow-ups", async ({ page }) => {
    await openHome(page, 390, 844, { chatFirst: true, cards: THREE, first: "reminder" });
    await openSheet(page);
    await expect(panel(page).getByText("Reminder: call the printer vendor.")).toBeVisible({ timeout: 30_000 });
    await expect(panel(page).getByText("Follow up", { exact: true })).toBeVisible({ timeout: 30_000 });
    for (const label of LABELS) await expect(panel(page).getByRole("button", { name: label, exact: true })).toHaveCount(0);
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
