import fs from "node:fs";
import path from "node:path";
import { test, type Page, type Route } from "@playwright/test";
const OUT = process.env.UX_OUT!;
const SESSION = {
  user: { email: "asha@example.com", name: "Asha Rao" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const TASK_ID = "11111111-1111-4111-8111-111111111111";
const REMINDER_ID = "22222222-2222-4222-8222-222222222222";
const MY_WORK_ON = ["1", "true", "on", "yes"].includes((process.env.NEXT_PUBLIC_PROJECTS_MY_WORK ?? "").toLowerCase());

interface Store {
  creates: Record<string, unknown>[];
  batches: Record<string, unknown>[];
  reminders: Record<string, unknown>[];
  resolves: { id: string; body: Record<string, unknown> }[];
  trashed: string[];
  atomized: string[];
  inbox: number;
}

async function setup(page: Page): Promise<Store> {
  const store: Store = { creates: [], batches: [], reminders: [], resolves: [], trashed: [], atomized: [], inbox: 5 };
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-dock", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.setItem("cc-my-day-feed", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  });
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const task = (id: string, title: string) => ({ id, title, disposition: "INBOX", project_id: null });
    if (path === "/api/projects/my/tasks" && req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      store.creates.push(body);
      store.inbox += 1;
      return json({ id: TASK_ID, title: body.title }, 201);
    }
    if (path === "/api/projects/my/tasks/batch" && req.method() === "POST") {
      const body = req.postDataJSON() as { items: { title: string }[] };
      store.batches.push(body);
      store.inbox += body.items.length;
      return json({ rows: body.items.map((it, i) => task(`3333333${i}-3333-4333-8333-333333333333`, it.title)), total: body.items.length });
    }
    if (path === `/api/projects/my/tasks/${TASK_ID}`) {
      return json(task(TASK_ID, String((store.creates.at(-1) ?? {}).title ?? "")));
    }
    if (path === `/api/projects/tasks/${TASK_ID}/personal` && req.method() === "PATCH") {
      const body = req.postDataJSON() as { disposition?: string };
      if (body.disposition === "TRASH") {
        store.trashed.push(TASK_ID);
        store.inbox -= 1;
      }
      return json(task(TASK_ID, ""));
    }
    if (path === "/api/projects/my/reminders" && req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      store.reminders.push(body);
      return json({ id: REMINDER_ID, title: body.title, fire_at: body.fire_at, state: "scheduled" }, 201);
    }
    if (path === `/api/projects/my/reminders/${REMINDER_ID}/resolve`) {
      store.resolves.push({ id: REMINDER_ID, body: req.postDataJSON() as Record<string, unknown> });
      return json({ id: REMINDER_ID, state: "dropped" });
    }
    if (path === "/api/tasks/ai/atomize") {
      const body = req.postDataJSON() as { text: string };
      store.atomized.push(body.text);
      return json({
        items: [
          { title: "Buy milk", verdict: "new", score: 0 },
          { title: "Call the lab", verdict: "new", score: 0 },
        ],
        used_llm: true,
      });
    }
    if (path === "/api/projects/my/summary") {
      return json({ tasks: store.inbox, by_category: {}, by_disposition: { inbox: store.inbox } });
    }
    if (path === "/api/projects/my/inbox") return json({ rows: [], total: 0 });
    if (path === "/api/shell/feed") {
      return json({ cards: [], groups: [], needs: { count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] }, sources: {}, capped: [] });
    }
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] });
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
  return store;
}


const CAL = (() => { const d = new Date(); const at = (h: number, m = 0) => { const x = new Date(d); x.setHours(h, m, 0, 0); return x.toISOString(); };
  return { rows: [
    { id: "c1", title: "Stand-up", scheduled_start: at(9, 30), scheduled_end: at(9, 45), disposition: "NEXT" },
    { id: "c2", title: "Design review with Kim and Ravi", scheduled_start: at(11), scheduled_end: at(12), disposition: "NEXT" },
  ], total: 2 }; })();
const CARD = { signal_id: "tasks:t1", app: "tasks", kind: "overdue", folded: [], group: "needs_you", score: 80, title: "Send the quote", why: "Due yesterday. 1 day late.", at: new Date(Date.now() - 30 * 3600000).toISOString(), href: "/projects?task=t1", acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "t1" }], state: "seen", updated: false, still_open: false, visits: 1, state_fp: "a41f09c2" };

async function shoot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, name + ".png") });
}
async function open(page: Page, hide: boolean) {
  await setup(page);
  await page.route("**/api/shell/feed", (r: Route) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ cards: [CARD], groups: [{ id: "needs_you", count: 1 }], needs: { count: 1, total: 1, items: [], sources: {}, totals: {}, capped: [] }, sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "absent", calendar: "ok" }, capped: [] }) }));
  await page.route("**/api/projects/my/calendar**", (r: Route) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CAL) }));
  await page.clock.setFixedTime(new Date(2026, 10, 7, 10, 0, 0));
  await page.goto("/");
  await page.getByTestId("my-day").waitFor({ timeout: 120_000 });
  if (hide) await page.addStyleTag({ content: "[data-testid=my-day-capture]{display:none !important}" });
  await page.waitForTimeout(2500);
}
const modes: [string, (p: Page) => Promise<void>][] = [
  ["light", async (p) => { await p.evaluate(() => document.documentElement.classList.add("light")); }],
  ["dark", async () => {}],
];
test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  for (const hide of [true, false]) {
    const tag = hide ? "before" : "after";
    for (const [m, apply] of modes) {
      test(`${tag} ${m}`, async ({ page }) => { test.setTimeout(240_000); await open(page, hide); await apply(page); await shoot(page, `${tag}--${m}-1440`); });
    }
    test(`${tag} compact`, async ({ page }) => { test.setTimeout(240_000); await open(page, hide); await page.evaluate(() => { document.documentElement.classList.add("light"); document.documentElement.style.setProperty("--ui-scale", "0.875"); }); await shoot(page, `${tag}--compact-1440`); });
    test(`${tag} accent`, async ({ page }) => { test.setTimeout(240_000); await open(page, hide); await page.evaluate(() => { document.documentElement.classList.add("light"); document.documentElement.style.setProperty("--primary", "hsl(280 70% 55%)"); }); await shoot(page, `${tag}--accent-1440`); });
  }
  test("after states", async ({ page }) => {
    test.setTimeout(240_000);
    await open(page, false);
    await page.evaluate(() => document.documentElement.classList.add("light"));
    const box = page.getByRole("textbox", { name: "Capture to my Inbox" });
    await box.focus(); await box.fill("call the bank at 3 pm");
    await shoot(page, "state--typed-light-1440");
    await box.press("Enter");
    await page.getByText("A reminder is set for 3 pm today.").waitFor();
    await shoot(page, "state--toast-light-1440");
    await page.waitForTimeout(500);
    await box.fill("buy milk and then call the lab");
    await box.press("Shift+Enter");
    await page.getByText("Review before adding").waitFor();
    await shoot(page, "state--review-light-1440");
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.documentElement.classList.remove("light"));
    await box.fill("call the bank at 3 pm"); await box.focus();
    await shoot(page, "state--typed-dark-1440");
  });
});
test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  for (const hide of [true, false]) {
    test(`${hide ? "before" : "after"} phone`, async ({ page }) => { test.setTimeout(240_000); await open(page, hide); await page.evaluate(() => document.documentElement.classList.add("light")); await shoot(page, `${hide ? "before" : "after"}--light-390`); });
  }
  test("after phone typed", async ({ page }) => {
    test.setTimeout(240_000); await open(page, false);
    await page.evaluate(() => document.documentElement.classList.add("light"));
    const box = page.getByRole("textbox", { name: "Capture to my Inbox" });
    await box.tap(); await box.fill("buy milk and then call the lab");
    await shoot(page, "state--typed-light-390");
    await page.getByRole("button", { name: "Split with AI" }).tap();
    await page.getByText("Review before adding").waitFor();
    await shoot(page, "state--review-light-390");
  });
});
