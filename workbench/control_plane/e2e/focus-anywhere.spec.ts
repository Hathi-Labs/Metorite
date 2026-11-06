import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * CC-4, start focus from anywhere (My Day command centre).
 *
 * One focus session at a time. It starts from a My Day row, from the Now card,
 * and from the Projects task panel. A to-do company task moves to In progress
 * on its board, with an Undo. The capture button of the focus room works on
 * every route. The flags are build-time values, so a dev build lets one
 * browser turn them on through `localStorage`, as `my-day-feed.spec.ts` does.
 *
 * Mutations, run by hand on 2026-11-07, each makes one test fail:
 *   1. `useStartFocus` skips the `lensSetStatusId` write: the status PATCH
 *      test fails (no PATCH to s-prog).
 *   2. The Undo handler does not write the old status back: the Undo test
 *      fails (no PATCH to s-todo).
 *   3. `enterFocusSession` is not called for a second task: the "replaces"
 *      test fails (the dock still names the first task).
 *   4. `FocusMode` shows the capture button on `/tasks*` only (the old
 *      `captureHere` rule): the capture tests fail.
 *   5. The panel's Start focus button is removed from `TaskPanel`: the panel
 *      test fails.
 */

const SESSION = { user: { email: "asha@example.com", name: "Asha Rao" }, expires: "2099-01-01T00:00:00.000Z" };
const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "calendar"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const at = (mins: number) => new Date(Date.now() + mins * 60_000).toISOString();

/** A task row as `/projects/my/tasks/{id}` and `/projects/my/calendar` send it. */
const raw = (id: string, title: string, startMin: number, over: Record<string, unknown> = {}) => ({
  id,
  title,
  disposition: "NEXT",
  project_id: "p2",
  root_project_id: "p1",
  project_name: "Fracktal",
  status_id: "s-todo",
  status_category: "todo",
  workflow_stage: "To do",
  scheduled_start: at(startMin),
  scheduled_end: at(startMin + 30),
  is_mine: true,
  created_at: at(-1000),
  updated_at: at(-500),
  ...over,
});

const STATUSES = [
  { id: "s-todo", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: false },
  { id: "s-prog", project_id: "p2", name: "In progress", color: "amber", position: 30, category: "in_progress", is_default: false },
  { id: "s-done", project_id: "p2", name: "Done", color: "green", position: 40, category: "done", is_default: false },
];

interface Calls {
  /** Each PATCH to a task, with its body. */
  taskPatches: { id: string; body: Record<string, unknown> }[];
  /** Each PATCH to the personal overlay, with its body. */
  personalPatches: { id: string; body: Record<string, unknown> }[];
  /** Each POST to `/complete`. */
  completes: string[];
}

async function setup(page: Page, tasks: Record<string, ReturnType<typeof raw>>): Promise<Calls> {
  const calls: Calls = { taskPatches: [], personalPatches: [], completes: [] };
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
    const path = new URL(req.url()).pathname.replace(/^\/api\//, "");
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "shell/feed") return json({ cards: [], groups: [], needs: { count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] }, sources: {}, capped: [] });
    if (path === "shell/needs") return json({ count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] });
    if (path === "auth/me/shell") return json(LAYOUT);
    if (path === "auth/me") return json(MEMBER);
    if (path === "auth/session") return json(SESSION);
    if (path === "health") return json({ gateway: "up" });
    if (path === "accounts") return json({ enabled: false });
    if (path === "settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "projects/my/calendar") return json({ rows: Object.values(tasks), total: Object.keys(tasks).length });
    if (path === "projects/my/inbox") return json({ rows: [], total: 0 });
    if (path === "projects/nodes/p2/statuses") return json({ rows: STATUSES, total: 3, counts: {}, owner_id: "p1" });
    if (path === "projects/tree") return json({ rows: [{ id: "p1", name: "Hathi Labs", parent_project_id: null, kind: "project", children: [{ id: "p2", name: "Fracktal", parent_project_id: "p1", kind: "project", children: [] }] }], total: 1 });
    let m = path.match(/^projects\/tasks\/([^/]+)\/personal$/);
    if (m && req.method() === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      calls.personalPatches.push({ id: m[1], body });
      // The server keeps the stamps, so a later read of the row shows them.
      if (tasks[m[1]]) Object.assign(tasks[m[1]], body);
      return json(tasks[m[1]] ?? {});
    }
    m = path.match(/^projects\/tasks\/([^/]+)\/complete$/);
    if (m && req.method() === "POST") {
      calls.completes.push(m[1]);
      return json(tasks[m[1]] ?? {});
    }
    m = path.match(/^projects\/tasks\/([^/]+)$/);
    if (m && req.method() === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      calls.taskPatches.push({ id: m[1], body });
      // The server keeps the new stage, so a later read of the row shows it.
      const lane = STATUSES.find((s) => s.id === body.status_id);
      if (lane && tasks[m[1]]) {
        Object.assign(tasks[m[1]], { status_id: lane.id, status_category: lane.category, workflow_stage: lane.name });
      }
      return json(tasks[m[1]] ?? {});
    }
    if (m && req.method() === "GET") return json({ ...tasks[m[1]], tags: [], assignees: [], custom_fields: {}, subtasks: { done: 0, total: 0 }, blocked_by_count: 0 });
    m = path.match(/^projects\/my\/tasks\/([^/]+)$/);
    if (m && tasks[m[1]]) return json(tasks[m[1]]);
    if (path.startsWith("projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "chat/active-sessions") return json([]);
    return json({ rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] });
  });
  return calls;
}

/** The dock's body: the task title and the clock. The maximize icon beside it has the same name. */
const dock = (page: Page) => page.locator('button[title="Expand focus mode"]');
const nowCard = (page: Page) => page.getByTestId("now-card");

test.describe("start focus from anywhere (CC-4)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a My Day row starts a session, the dock names the task, and the Now card runs", async ({ page }) => {
    const tasks = { t1: raw("t1", "Send the quote", -5), t3: raw("t3", "Design review", 60) };
    await setup(page, tasks);
    await page.goto("/");
    // Idle: the card offers Start focus on today's first block.
    await expect(nowCard(page)).toHaveAttribute("data-now-state", "idle");
    await page.getByTestId("today-start-focus").first().click();
    await expect(dock(page)).toContainText("Send the quote");
    await expect(nowCard(page)).toHaveAttribute("data-now-state", "running");
    await expect(page.getByTestId("now-title")).toHaveText("Send the quote");
    await expect(page.getByTestId("now-clock")).toHaveText(/^\d+:\d\d$/);
    await expect(page.getByTestId("now-up-next")).toContainText("Design review");
  });

  test("a to-do company task moves to In progress with one PATCH, and Undo puts it back", async ({ page }) => {
    const calls = await setup(page, { t1: raw("t1", "Send the quote", -5) });
    await page.goto("/");
    await page.getByTestId("now-start").click();
    await expect.poll(() => calls.taskPatches).toEqual([{ id: "t1", body: { status_id: "s-prog" } }]);
    await expect(page.getByText("Moved to In progress")).toBeVisible();
    await expect(nowCard(page)).toContainText("In progress on the board");
    await page.getByRole("button", { name: "Undo" }).click({ timeout: 5_000 });
    await expect.poll(() => calls.taskPatches.map((p) => p.body.status_id)).toEqual(["s-prog", "s-todo"]);
  });

  test("a task already in progress sends no status PATCH", async ({ page }) => {
    const calls = await setup(page, {
      t1: raw("t1", "Already going", -5, { status_id: "s-prog", status_category: "in_progress" }),
    });
    await page.goto("/");
    await page.getByTestId("now-start").click();
    await expect(dock(page)).toContainText("Already going");
    await page.waitForTimeout(800);
    expect(calls.taskPatches).toEqual([]);
  });

  test("a second start replaces the session: one dock, the first task's timer stops", async ({ page }) => {
    const calls = await setup(page, {
      t1: raw("t1", "Send the quote", -5, { status_id: "s-prog", status_category: "in_progress" }),
      t3: raw("t3", "Design review", 60, { status_id: "s-prog", status_category: "in_progress" }),
    });
    await page.goto("/");
    const rows = page.getByTestId("today-start-focus");
    await rows.nth(0).click();
    await expect(dock(page)).toContainText("Send the quote");
    await rows.nth(1).click();
    await expect(dock(page)).toHaveCount(1); // one dock
    await expect(dock(page)).toContainText("Design review");
    await expect(page.getByTestId("now-title")).toHaveText("Design review");
    // The first task's actual end was stamped when the second began.
    await expect
      .poll(() => calls.personalPatches.some((p) => p.id === "t1" && typeof p.body.actual_end === "string" && p.body.actual_end !== ""))
      .toBe(true);
  });

  test("the Now card pauses, adds 10 minutes, and Done closes the session", async ({ page }) => {
    const calls = await setup(page, { t1: raw("t1", "Send the quote", -5, { status_id: "s-prog", status_category: "in_progress" }) });
    await page.goto("/");
    await page.getByTestId("now-start").click();
    await expect(nowCard(page)).toContainText("of 25:00");
    await page.getByTestId("now-extend").click();
    await expect(nowCard(page)).toContainText("of 35:00");
    await page.getByTestId("now-pause").click();
    await expect(nowCard(page)).toHaveAttribute("data-now-state", "paused");
    await expect(nowCard(page)).toContainText("paused");
    await page.getByTestId("now-pause").click();
    await expect(nowCard(page)).toHaveAttribute("data-now-state", "running");
    await page.getByTestId("now-done").click();
    await expect(dock(page)).toHaveCount(0);
    await expect(nowCard(page)).toHaveAttribute("data-now-state", "idle");
    await expect.poll(() => calls.completes).toEqual(["t1"]);
  });

  test("the focus room's capture button opens capture on / and on /projects", async ({ page }) => {
    test.setTimeout(150_000);
    await setup(page, { t1: raw("t1", "Send the quote", -5, { status_id: "s-prog", status_category: "in_progress" }) });
    await page.goto("/");
    await page.getByTestId("now-start").click();
    await page.getByTestId("now-open-room").click();
    await page.getByRole("button", { name: "Capture a thought" }).click();
    await expect(page.getByLabel("Capture to my task inbox")).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    // Minimize, then go to Projects: the session is global, and so is capture.
    await page.getByRole("button", { name: "Minimize focus mode" }).click();
    await page.getByRole("link", { name: /^Projects/ }).first().click();
    await expect(page).toHaveURL(/\/projects/, { timeout: 60_000 });
    await expect(dock(page)).toContainText("Send the quote");
    await dock(page).click();
    await page.getByRole("button", { name: "Capture a thought" }).click();
    await expect(page.getByLabel("Capture to my task inbox")).toBeVisible();
  });

  test("the Projects task panel starts the same session", async ({ page }) => {
    const calls = await setup(page, { t2: raw("t2", "Per-person credit limits", -5) });
    await page.goto("/projects?task=t2");
    await page.getByTestId("task-start-focus").click({ timeout: 30_000 });
    await expect(page.getByText("Moved to In progress")).toBeVisible();
    await expect.poll(() => calls.taskPatches.map((p) => p.body.status_id)).toContain("s-prog");
    await page.getByRole("button", { name: "Minimize focus mode" }).click();
    await expect(dock(page)).toContainText("Per-person credit limits");
  });
});
