import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { CONTEXTS } from "./harness";

/** CO-13 capture rig: the real chat page, stubbed gateway. Untracked. */
const OUT = path.resolve(__dirname, "../../../../.co13_captures");
const H = 3_600_000;
const iso = (ago: number) => new Date(Date.now() - ago).toISOString();
type Mark = { id: string | null; title: string | null } | null;
const row = (id: string, title: string, ago: number, continuedFrom: Mark = null) => ({
  id, agentName: "orchestrator", title, lastPreview: `Last words of ${title}`, messageCount: 6,
  createdAt: iso(ago + H), updatedAt: iso(ago), visibility: "private", isOwner: true,
  participantCount: 1, hasUserMessage: true, archivedAt: null, pinnedAt: null, continuedFrom,
});
const ACTIVE = "Quarterly invoice follow-ups";
const SUFFIX: Record<string, string> = {
  "light-1440": "light", "compact-1440": "compact", "accent-violet-1440": "accent", "mobile-390": "390",
};

async function stub(page: Page, mark: Mark) {
  const sessions = [
    row("s1", ACTIVE, 2 * H, mark),
    row("s3", "Vendor contract review", 30 * H),
    row("s4", "Standup notes, Tuesday", 72 * H),
  ];
  await page.addInitScript(() => {
    window.localStorage.clear();
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav", "cc-shell-chat-first"]) window.localStorage.setItem(k, "1");
  });
  await page.route("**/api/auth/me/shell", (r) => r.fulfill({ json: { answered: "skipped", chat_first_message_at: "2026-11-01T00:00:00Z", buddy_hello_at: "2026-11-01T00:00:00Z", chat_pin: true } }));
  await page.route("**/api/auth/me", (r) => r.fulfill({ json: {
    email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
    organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
    roles: ["owner"], legacy_role: "executive", features: ["chat"], features_denied: [],
    agents: ["orchestrator"], permissions: ["*"], capabilities: [], denied: [], is_admin: true,
  } }));
  await page.route("**/api/health", (r) => r.fulfill({ json: { gateway: "up", build: "e2e" } }));
  await page.route("**/api/chat/active-sessions", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions/*/room", (r) => r.fulfill({ status: 404, json: { detail: "not a room" } }));
  await page.route("**/api/chat/sessions/*/messages**", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/chat/sessions**", (r) =>
    r.request().url().match(/\/room|\/messages|\/pin/) ? r.fallback()
      : r.fulfill({ json: r.request().method() === "GET" ? sessions : { ok: true, saved: 1, unchanged: [] } }));
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function run(page: Page, state: string, mark: Mark, want: string[], lead: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page, mark);
  await page.goto("/chat");
  const rowBtn = page.getByRole("button", { name: new RegExp(ACTIVE) }).first();
  await expect(rowBtn).toBeVisible({ timeout: 90_000 });
  await rowBtn.click().catch(() => {});
  const markEl = page.locator("[data-continued-mark]");
  await expect(markEl.first()).toBeVisible({ timeout: 20_000 });
  await expect(markEl.first()).toContainText(lead);
  for (const ctx of CONTEXTS.filter((c) => want.includes(c.name))) {
    await page.setViewportSize({ width: ctx.width, height: ctx.height });
    if (ctx.apply) await ctx.apply(page);
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, `co13-${state}-${SUFFIX[ctx.name]}.png`) });
    if (ctx.undo) await ctx.undo(page);
  }
}

test("readable source", async ({ page }) => {
  test.setTimeout(240_000);
  await run(page, "readable", { id: "s3", title: "Vendor contract review" }, Object.keys(SUFFIX), "Continued from");
  await expect(page.locator("[data-continued-mark] button")).toHaveText("Vendor contract review");
});

test("unreadable source", async ({ page }) => {
  test.setTimeout(240_000);
  await run(page, "unreadable", { id: null, title: null }, ["light-1440", "mobile-390"], "Continued from an earlier chat");
  await expect(page.locator("[data-continued-mark] button")).toHaveCount(0);
});
