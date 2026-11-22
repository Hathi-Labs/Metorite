import { expect, test } from "@playwright/test";
import path from "node:path";
import { CONTEXTS } from "./harness";

/** CO-11 capture rig: the real chat page with a stubbed gateway. Untracked. */
const OUT = path.resolve(__dirname, "../../../../.co11_captures");
const WANT: Record<string, string> = {
  "light-1440": "co11-light",
  "compact-1440": "co11-compact",
  "accent-violet-1440": "co11-accent",
  "mobile-390": "co11-390",
};
const H = 3_600_000;
const D = 24 * H;
const iso = (ago: number) => new Date(Date.now() - ago).toISOString();
const row = (id: string, title: string, ago: number, pinnedAgo?: number) => ({
  id, agentName: "orchestrator", title, lastPreview: `Last words of ${title}`, messageCount: 6,
  createdAt: iso(ago + H), updatedAt: iso(ago), visibility: "private", isOwner: true,
  participantCount: 1, hasUserMessage: true, archivedAt: null,
  pinnedAt: pinnedAgo === undefined ? null : iso(pinnedAgo),
});
const SESSIONS = [
  row("s1", "Quarterly invoice follow-ups", 2 * H),
  row("s2", "Draft the onboarding plan for Sharath", 5 * H),
  row("s3", "Vendor contract review", 1 * D + 2 * H, 3 * D),
  row("s4", "Standup notes, Tuesday", 3 * D),
  row("s5", "Customer call summary: DEWiN", 9 * D, 10 * D),
];

test("chat pin UI in four contexts", async ({ page }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    window.localStorage.clear();
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav", "cc-shell-chat-first"]) window.localStorage.setItem(k, "1");
  });
  const ME = "dev@e2e.test";
  await page.route("**/api/auth/me/shell", (r) => r.fulfill({ json: { answered: "skipped", chat_first_message_at: "2026-11-01T00:00:00Z", buddy_hello_at: "2026-11-01T00:00:00Z", chat_pin: true } }));
  await page.route("**/api/auth/me", (r) => r.fulfill({ json: {
    email: ME, user_id: "dev", authenticated: true, is_active: true,
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
      : r.fulfill({ json: r.request().method() === "GET" ? SESSIONS : { ok: true, saved: 1, unchanged: [] } }));
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));

  await page.goto("/chat");
  const pinned = page.getByText("Pinned", { exact: true }).first();
  await expect(pinned).toBeVisible({ timeout: 60_000 });
  const rowBtn = page.getByRole("button", { name: /Quarterly invoice follow-ups/ }).first();
  await rowBtn.click().catch(() => {});
  await page.waitForTimeout(800);

  for (const ctx of CONTEXTS.filter((c) => WANT[c.name])) {
    await page.setViewportSize({ width: ctx.width, height: ctx.height });
    if (ctx.apply) await ctx.apply(page);
    await page.waitForTimeout(700);
    const target = page.getByText("Vendor contract review").first();
    if (await target.isVisible().catch(() => false)) await target.hover().catch(() => {});
    await page.waitForTimeout(300);
    if (ctx.name === "mobile-390") {
      // The phone's picker sheet first (Pinned group), then the "…" menu (Pin act).
      await page.getByRole("button", { name: /Quarterly invoice follow/ }).first().click().catch(() => {});
      await page.waitForTimeout(600);
      await page.screenshot({ path: path.join(OUT, "co11-390-list.png") });
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      await page.getByRole("button", { name: "More", exact: true }).first().click().catch(() => {});
      await page.waitForTimeout(500);
    }
    await page.screenshot({ path: path.join(OUT, `${WANT[ctx.name]}.png`) });
    if (ctx.undo) await ctx.undo(page);
  }
});
