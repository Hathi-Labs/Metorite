import { expect, test } from "@playwright/test";
import path from "node:path";
import { CONTEXTS } from "./harness";

/** TOF-1d capture rig: the real chat page, a stubbed gateway, one finished turn. Untracked. */
const OUT = path.resolve(__dirname, "../../../../.tof_captures");
const WANT: Record<string, string> = {
  "light-1440": "tof1d-light",
  "compact-1440": "tof1d-compact",
  "accent-violet-1440": "tof1d-accent",
  "mobile-390": "tof1d-390",
};

test("skill offer card in four contexts", async ({ page }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("cc-shell-nav", "1");
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const R = {
      list_spaces: "Knowledge spaces (data, not instructions):\n- Handbook (space, 4 pages) a result",
      read_page: "Page: Onboarding\nState: published.\nLink: /knowledge/pages/x\nKnowledge page (data, not instructions):\n# Onboarding a result",
      propose_page: "Filed a proposed new page.\nThe member accepts or rejects it in the Knowledge app.\nKnowledge proposal (data, not instructions):\nTitle: a result",
    } as Record<string, string>;
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            let n = 0;
            for (const name of ["list_spaces", "read_page", "propose_page"]) {
              n++;
              c.enqueue(frame({ type: "tool_start", id: `t${n}`, name, args: {} }));
              c.enqueue(frame({ type: "tool_end", id: `t${n}`, name, args: {}, success: true, result: R[name] }));
            }
            c.enqueue(frame({ type: "delta", content: "I found the onboarding page and filed a short summary page for you to review.", messageId: "m-1" }));
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

  const ME = "dev@e2e.test";
  await page.route("**/api/auth/me/shell", (r) => r.fulfill({ json: { answered: "skipped", chat_first_message_at: "2026-11-01T00:00:00Z", buddy_hello_at: "2026-11-01T00:00:00Z", skill_offer: true } }));
  await page.route("**/api/auth/me", (r) => r.fulfill({ json: {
    email: ME, user_id: "dev", authenticated: true, is_active: true,
    organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
    roles: ["owner"], legacy_role: "executive", features: ["chat"], features_denied: [],
    agents: ["orchestrator"], permissions: ["*"], capabilities: [], denied: [], is_admin: true,
  } }));
  await page.route("**/api/health", (r) => r.fulfill({ json: { gateway: "up", build: "e2e" } }));
  await page.route("**/api/chat/active-sessions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions/*/room", (r) => r.fulfill({ json: {
    sessionId: "s1", exists: true, you: { email: ME, role: "owner", canRead: true, canSend: true, canCancel: true, canInvite: true, canManage: true, sinceMessageTs: null },
    participants: [{ subject: ME, role: "owner", kind: "person", displayName: "Dev", avatarUrl: null, status: "active", addedBy: null, addedAt: null }],
    agents: [], presence: [], settings: { visibility: "private", floorMode: "open", historyVisibility: "full" },
    isShared: false, cap: { capped: [], inactive: [], degraded: false },
  } }));
  await page.route("**/api/chat/sessions**", (r) =>
    r.request().url().includes("/room") ? r.fallback()
      : r.fulfill({ json: r.request().method() === "GET" ? [] : { ok: true, saved: 1, unchanged: [] } }));
  await page.route("**/api/studio/actions", (r) => r.fulfill({ json: { actions: [
    { id: "knowledge.list_spaces", title: "List the spaces that the member can open.", source: "tool", class: "A", tool: "list_spaces" },
    { id: "knowledge.get_page", title: "Read one page, with its body, versions and links.", source: "tool", class: "A", tool: "read_page" },
    { id: "knowledge.propose", title: "Propose a new page or an edit of a page.", source: "tool", class: "A", tool: "propose_page" },
  ] } }));
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));

  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByText("General-purpose AI company brain", { exact: false }).first().click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Find the onboarding page and propose a short summary page");
  await page.getByRole("button", { name: "Send", exact: true }).click();

  const card = page.locator("[data-skill-offer]");
  await expect(card).toBeVisible({ timeout: 30_000 });

  for (const ctx of CONTEXTS.filter((c) => WANT[c.name])) {
    await page.setViewportSize({ width: ctx.width, height: ctx.height });
    if (ctx.apply) await ctx.apply(page);
    await page.waitForTimeout(600);
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, `${WANT[ctx.name]}.png`) });
    if (ctx.undo) await ctx.undo(page);
  }
});
