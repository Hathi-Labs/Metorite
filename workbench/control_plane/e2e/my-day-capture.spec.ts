import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-CC CC-1, the capture box of My Day (`src/lib/shell/MyDayCapture.tsx`,
 * Metorite task 1968). The flags are build-time values, so a dev build lets
 * one browser turn them on through `localStorage`, as `my-day-feed.spec.ts`
 * does. The router below is the stub style of that spec.
 *
 * Each test is written so the obvious wrong build fails it. The mutations were
 * run by hand on 2026-11-07, each on its own, and each turned the named test red:
 *
 *   1. Enter files ONE create, the box clears, a toast shows, the Inbox count
 *      reads again.
 *      Mutation: file the line twice in `file()` (call `captureLine` twice).
 *   2. A named time sets ONE reminder at that time, today, and Undo drops the
 *      reminder AND trashes the Inbox item.
 *      Mutations: (a) skip `createReminder` in `file()`. (b) leave the
 *      `runReminderAct` call out of `undoCapture`. (c) leave `undoLastCapture`
 *      out of `undoCapture`.
 *   3. A past time and a plain line make no reminder.
 *      Mutation: drop the `<= now.getTime()` test in `namedTimeToday`.
 *   4. Shift+Enter opens the review of the AI split, files nothing before the
 *      member confirms, and then files through the batch route.
 *      Mutation: make Shift+Enter call `file()` (it posts one create).
 *   5. "Inbox N · Clarify" goes to the Inbox.
 *      Mutation: point the button at "/people".
 *   6. On a phone, the box fits 390 px, and the AI split button works by touch.
 *      Mutation: give the wrapper of the Input `min-w-[480px]`.
 */

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

const box = (page: Page) => page.getByRole("textbox", { name: "Capture to my Inbox" });
const clarify = (page: Page) => page.getByTestId("my-day-clarify");

/** The browser clock stops at 10:00 on 7 Nov 2026, so "3 pm" is later today. */
async function morning(page: Page) {
  await page.clock.setFixedTime(new Date(2026, 10, 7, 10, 0, 0));
}

test.describe("My Day capture, desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Enter files ONE create to the Inbox, clears the box, toasts, and the count reads again", async ({ page }) => {
    const store = await setup(page);
    await morning(page);
    await page.goto("/");
    await expect(clarify(page)).toHaveText("Inbox 5 · Clarify");
    await box(page).fill("Order printer paper");
    await box(page).press("Enter");
    await expect(page.getByText("Filed to your Inbox")).toBeVisible();
    await expect(box(page)).toHaveValue("");
    expect(store.creates).toHaveLength(1);
    expect(store.creates[0]).toMatchObject({ title: "Order printer paper" });
    // A capture states no disposition: the route files it as INBOX (D170).
    expect(store.creates[0].disposition ?? "INBOX").toBe("INBOX");
    expect(store.reminders).toHaveLength(0);
    await expect(clarify(page)).toHaveText("Inbox 6 · Clarify");
  });

  test("a named time also sets one reminder today, and Undo drops it and trashes the item", async ({ page }) => {
    const store = await setup(page);
    await morning(page);
    await page.goto("/");
    await box(page).fill("call the bank at 3 pm");
    await box(page).press("Enter");
    await expect(page.getByText("A reminder is set for 3 pm today.")).toBeVisible();
    expect(store.creates).toHaveLength(1);
    expect(store.reminders).toHaveLength(1);
    expect(store.reminders[0]).toMatchObject({ title: "call the bank at 3 pm", kind: "action" });
    expect(String(store.reminders[0].fire_at)).toMatch(/^2026-11-07T15:00:00/);

    await page.getByRole("button", { name: "Undo" }).click();
    await expect(page.getByText("Undone")).toBeVisible();
    await expect.poll(() => store.resolves).toEqual([{ id: REMINDER_ID, body: { action: "drop" } }]);
    await expect.poll(() => store.trashed).toEqual([TASK_ID]);
    await expect(clarify(page)).toHaveText("Inbox 5 · Clarify");
  });

  test("a time that has passed, and a line with no time, set no reminder", async ({ page }) => {
    const store = await setup(page);
    await morning(page);
    await page.goto("/");
    await box(page).fill("call the bank at 9 am");
    await box(page).press("Enter");
    await expect(page.getByText("Filed to your Inbox")).toBeVisible();
    // The member typed a time, so the toast says that nothing was set (verifier P2).
    await expect(page.getByText("No reminder was set. That time has passed today.")).toBeVisible();
    await expect(box(page)).toHaveValue("");
    expect(store.creates).toHaveLength(1);
    expect(store.reminders).toHaveLength(0);
  });

  test("Shift+Enter opens the review of the AI split, files nothing until the member confirms", async ({ page }) => {
    const store = await setup(page);
    await morning(page);
    await page.goto("/");
    await box(page).fill("buy milk and then call the lab");
    await box(page).press("Shift+Enter");
    await expect(page.getByText("Review before adding")).toBeVisible();
    await expect(page.locator('input[value="Buy milk"]')).toBeVisible();
    await expect(page.locator('input[value="Call the lab"]')).toBeVisible();
    expect(store.atomized).toEqual(["buy milk and then call the lab"]);
    expect(store.creates).toHaveLength(0);
    expect(store.batches).toHaveLength(0);

    await page.getByRole("button", { name: "Add 2 to my task inbox" }).click();
    await expect.poll(() => store.batches).toEqual([{ items: [{ title: "Buy milk" }, { title: "Call the lab" }] }]);
    await expect(page.getByText("Filed 2 items to your Inbox")).toBeVisible();
    await expect(box(page)).toHaveValue("");
    expect(store.creates).toHaveLength(0);
  });

  test('"Inbox N · Clarify" goes to the Inbox', async ({ page }) => {
    await setup(page);
    await morning(page);
    await page.goto("/");
    await clarify(page).click();
    // With the My work flag off, the Inbox is /tasks. With it on, it is the My task inbox of Projects.
    await page.waitForURL(MY_WORK_ON ? /\/projects\?app=my-inbox/ : /\/(tasks|projects)/);
  });
});

test.describe("My Day capture, phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("the box fits 390 px and the AI split button opens the review by touch", async ({ page }) => {
    const store = await setup(page);
    await morning(page);
    await page.goto("/");
    await expect(box(page)).toBeVisible();
    const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(fits).toBe(true);
    const b = await box(page).boundingBox();
    expect(b!.x).toBeGreaterThanOrEqual(0);
    expect(b!.x + b!.width).toBeLessThanOrEqual(390);
    await box(page).fill("buy milk and then call the lab");
    await page.getByRole("button", { name: "Split with AI" }).tap();
    await expect(page.getByText("Review before adding")).toBeVisible();
    expect(store.creates).toHaveLength(0);
  });
});
