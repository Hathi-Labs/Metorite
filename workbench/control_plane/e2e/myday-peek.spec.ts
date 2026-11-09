import { expect, test, type Page, type Request, type Route } from "@playwright/test";

/**
 * PK-1, the panel on My Day (task 2469, D204, `my_day_feed.md` §11.5).
 *
 * The owner, 2026-11-09: a task card on My Day opened Projects, and the
 * member had to go back to My Day for the rest of the day. With
 * `NEXT_PUBLIC_MY_DAY_PEEK` on, a plain click on a task row opens the task in
 * a panel on My Day, and the path stays `/`.
 *
 * The flags are build-time values, so a dev build lets one browser turn them
 * on for itself through `localStorage`, as `my-day-feed.spec.ts` does. No
 * gateway runs here: one router answers every `/api` read.
 *
 * Each test is written so the obvious wrong build fails it:
 *   1. A click on a feed row opens the panel with the task, and the path
 *      stays `/`. A status change in the panel shows on the row after ✕.
 *   2. Escape closes the panel, and Back closes it too. A reload with
 *      `?peek=` opens it again.
 *   3. Ctrl-click opens Projects in a new tab, and opens no panel.
 *   4. With the flag off, the row opens Projects, as before.
 *   5. At 390 wide the panel is a sheet that ends at the bar's top edge, and
 *      a tap on a bar tab closes it.
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
  features: ["tasks", "projects", "email", "chat"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const TASK_ID = "6f0c1a52-3b9e-4c7d-9a10-2b5e8f4d1c33";
const LANES = [
  { id: "s1", project_id: "p1", name: "To do", color: "", position: 10, category: "todo", is_default: true },
  { id: "s2", project_id: "p1", name: "In progress", color: "", position: 20, category: "in_progress", is_default: false },
];

interface Store {
  status: "s1" | "s2";
  feedReads: number;
  patches: Record<string, unknown>[];
}

const laneName = (id: string) => LANES.find((l) => l.id === id)?.name ?? "";

function task(store: Store) {
  return {
    id: TASK_ID,
    project_id: "p1",
    root_project_id: "p1",
    task_number: 42,
    status_id: store.status,
    title: "Send the quote",
    tags: [],
    assignees: ["asha@example.com"],
    custom_fields: {},
    subtasks: { done: 0, total: 0 },
    blocked_by_count: 0,
  };
}

function card(store: Store) {
  return {
    signal_id: `tasks:${TASK_ID}`,
    app: "tasks",
    kind: "overdue",
    folded: [],
    group: "needs_you",
    score: 80,
    title: "Send the quote",
    // The row says the lane, so a change in the panel shows on the row.
    why: `Due yesterday. ${laneName(store.status)}.`,
    at: new Date(Date.now() - 30 * 3_600_000).toISOString(),
    href: `/projects?task=${TASK_ID}`,
    acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: TASK_ID }],
    state: "seen",
    updated: false,
    still_open: false,
    visits: 1,
    state_fp: "a41f09c2",
  };
}

const NEEDS = {
  count: 1,
  total: 1,
  items: [
    {
      id: `tasks:${TASK_ID}`,
      app: "tasks",
      kind: "overdue",
      title: "Send the quote",
      detail: null,
      href: `/projects?task=${TASK_ID}`,
      at: new Date(Date.now() - 30 * 3_600_000).toISOString(),
      act: "done",
      act_ref: TASK_ID,
    },
  ],
  sources: { tasks: "ok", projects: "ok", email: "ok" },
  totals: { tasks: 1, projects: 0, email: 0 },
  capped: [],
};

/** Every other `/api/projects` read: empty, in each shape the body reads. */
const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

async function setup(page: Page, opts: { peek?: boolean } = {}): Promise<Store> {
  const store: Store = { status: "s1", feedReads: 0, patches: [] };
  const peek = opts.peek ?? true;
  await page.addInitScript((on: boolean) => {
    for (const k of ["cc-shell-bar", "cc-shell-nav", "cc-shell-dock", "cc-my-day", "cc-my-day-feed"]) {
      localStorage.setItem(k, "1");
    }
    if (on) localStorage.setItem("cc-my-day-peek", "1");
    else localStorage.removeItem("cc-my-day-peek");
    localStorage.removeItem("cc-sidebar-collapsed");
  }, peek);
  await page.route("**/api/**", async (route: Route) => {
    const req: Request = route.request();
    const path = new URL(req.url()).pathname;
    const method = req.method().toUpperCase();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/shell/feed/state") return json({ written: 0 });
    if (path === "/api/shell/feed") {
      store.feedReads += 1;
      return json({
        cards: [card(store)],
        groups: [{ id: "needs_you", count: 1 }],
        needs: NEEDS,
        sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "absent", calendar: "ok" },
        capped: [],
      });
    }
    if (path === "/api/shell/needs") return json(NEEDS);
    if (path === `/api/projects/tasks/${TASK_ID}`) {
      if (method === "PATCH") {
        const body = req.postDataJSON() as Record<string, unknown>;
        store.patches.push(body);
        if (body.status_id === "s1" || body.status_id === "s2") store.status = body.status_id;
      }
      return json(task(store));
    }
    if (path === `/api/projects/my/tasks/${TASK_ID}/lanes`) return json({ rows: LANES, total: LANES.length });
    // Not in the member's lens as an overlay: the panel draws no strip.
    if (path === `/api/projects/my/tasks/${TASK_ID}`) return json({ detail: "No such task" }, 404);
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    if (path.startsWith("/api/projects/")) return json(EMPTY);
    return json(EMPTY);
  });
  return store;
}

const row = (page: Page) => page.getByTestId(`feed-card-tasks:${TASK_ID}`);
const rowLink = (page: Page) => row(page).getByRole("link", { name: "Send the quote" });
const panel = (page: Page) => page.locator("[data-my-day-peek]");
const panelTitle = (page: Page) => panel(page).getByRole("heading", { name: "Send the quote" });

test.describe("My Day panel (PK-1), desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a row opens the task in the panel, the path stays /, and a status change shows on the row after ✕", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const store = await setup(page);
    await page.goto("/");
    await expect(rowLink(page)).toBeVisible({ timeout: 60_000 });
    await expect(row(page)).toContainText("To do");

    await rowLink(page).click();
    await expect(panelTitle(page)).toBeVisible({ timeout: 30_000 });
    const url = new URL(page.url());
    expect(url.pathname).toBe("/");
    expect(url.searchParams.get("peek")).toBe(`task:${TASK_ID}`);
    // The shared header: the ref, and the way out to Projects.
    await expect(panel(page).getByText("#42")).toBeVisible();
    await expect(panel(page).getByRole("link", { name: "Open in Projects" })).toHaveAttribute(
      "href",
      `/projects?task=${TASK_ID}`,
    );
    // The page beside it stays: the row is still on screen.
    await expect(rowLink(page)).toBeVisible();

    // The shared body's Status control.
    await panel(page).getByRole("button", { name: "Status" }).click();
    await page.getByRole("option", { name: "In progress" }).click();
    await expect.poll(() => store.patches.length).toBeGreaterThan(0);
    expect(store.patches[0]).toMatchObject({ status_id: "s2" });

    const readsBefore = store.feedReads;
    await panel(page).getByRole("button", { name: "Close task" }).click();
    await expect(panel(page)).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get("peek")).toBeNull();
    // The rows read again on close, and the row shows the new lane.
    await expect.poll(() => store.feedReads).toBeGreaterThan(readsBefore);
    await expect(row(page)).toContainText("In progress");
  });

  test("Escape and Back close the panel, and a reload opens it again", async ({ page }) => {
    test.setTimeout(120_000);
    await setup(page);
    await page.goto("/");
    await expect(rowLink(page)).toBeVisible({ timeout: 60_000 });

    await rowLink(page).click();
    await expect(panelTitle(page)).toBeVisible({ timeout: 30_000 });
    // The focus is on the row the member clicked, on the page.
    await page.keyboard.press("Escape");
    await expect(panel(page)).toHaveCount(0);
    expect(new URL(page.url()).pathname).toBe("/");

    await rowLink(page).click();
    await expect(panelTitle(page)).toBeVisible();
    await page.goBack();
    await expect(panel(page)).toHaveCount(0);
    expect(new URL(page.url()).search).toBe("");

    await page.goto(`/?peek=task:${TASK_ID}`);
    await expect(panelTitle(page)).toBeVisible({ timeout: 60_000 });
  });

  test("Ctrl-click opens Projects in a new tab, and opens no panel", async ({ page, context }) => {
    test.setTimeout(120_000);
    await setup(page);
    await page.goto("/");
    await expect(rowLink(page)).toBeVisible({ timeout: 60_000 });
    const opened = context.waitForEvent("page");
    await rowLink(page).click({ modifiers: ["Control"] });
    const tab = await opened;
    // A new tab starts on about:blank, so wait for its address.
    await tab.waitForURL(/\/projects\?task=/, { timeout: 60_000 });
    expect(new URL(tab.url()).pathname).toBe("/projects");
    expect(new URL(tab.url()).searchParams.get("task")).toBe(TASK_ID);
    await tab.close();
    await expect(panel(page)).toHaveCount(0);
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("with the flag off, the row opens Projects", async ({ page }) => {
    test.setTimeout(120_000);
    await setup(page, { peek: false });
    await page.goto("/");
    await expect(rowLink(page)).toBeVisible({ timeout: 60_000 });
    await rowLink(page).click();
    await page.waitForURL(/\/projects\?task=/, { timeout: 60_000 });
    expect(new URL(page.url()).searchParams.get("task")).toBe(TASK_ID);
  });
});

test.describe("My Day panel (PK-1), phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the sheet ends at the bar's top edge, and a bar tab closes it", async ({ page }) => {
    test.setTimeout(120_000);
    await setup(page);
    await page.goto("/");
    await expect(page.locator("[data-phone-nav-bar]")).toBeVisible({ timeout: 60_000 });
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
    await expect(rowLink(page)).toBeVisible({ timeout: 60_000 });

    await rowLink(page).click();
    await expect(panelTitle(page)).toBeVisible({ timeout: 30_000 });
    expect(new URL(page.url()).pathname).toBe("/");
    const box = await page.evaluate(() => {
      const bar = document.querySelector("[data-phone-nav-bar]")!.getBoundingClientRect();
      const sheet = document.querySelector("[data-my-day-peek]")!.getBoundingClientRect();
      return { barTop: bar.top, sheetTop: sheet.top, sheetBottom: sheet.bottom, width: sheet.width };
    });
    expect(box.sheetBottom).toBeLessThanOrEqual(box.barTop + 0.5);
    expect(box.sheetTop).toBeLessThanOrEqual(1);
    expect(box.width).toBeCloseTo(390, 0);

    await page.locator("[data-phone-nav-bar] nav").getByRole("button", { name: "Menu", exact: true }).click();
    await expect(panel(page)).toHaveCount(0);
  });
});
