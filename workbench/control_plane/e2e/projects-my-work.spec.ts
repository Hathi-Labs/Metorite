import { expect, test, type Page } from "@playwright/test";

/**
 * My work and My tasks in the Projects sidebar (WS-64 OP-2 and OP-11,
 * `one_projects_app.md` §4.1, §9 OP-2 and §9 OP-11).
 *
 * ## The rules
 *
 * 1. With the flag on, the My work section holds two rows: "My task inbox"
 *    and "My tasks", each with the count of `GET /projects/my/summary`.
 * 2. My tasks is a roll-up space with a project's tabs: Overview, Board,
 *    List, Table and Calendar, the Needs attention lens and Reports.
 * 3. The chips (All, Today, Next, Waiting, Someday, Done) filter the rows,
 *    and the address keeps the chip.
 * 4. The board groups by stage. A drop writes the first status of that stage
 *    in the card's OWN project.
 * 5. Each row names its project. `/projects` lands on My tasks with Today,
 *    and the old ids (`?app=my-today`, `?app=my-waiting`) open their chip.
 * 6. At 390 px the bar reads Menu, My tasks, Capture, Search, Ask, and My
 *    tasks opens its List with the chip row.
 *
 * ## How to run it
 *
 * The flags are inlined at build time, so the dev server must start with them:
 *
 *     NEXT_PUBLIC_PROJECTS_MY_WORK=1 NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1 \
 *       npx playwright test e2e/projects-my-work.spec.ts
 *
 * Without the My work flag the file skips. A vitest test cannot see the page
 * (`src/app/projects/lib/myTasks.test.ts` and `myWork.test.ts` fence the rules).
 */

test.skip(
  !["1", "true", "on", "yes"].includes((process.env.NEXT_PUBLIC_PROJECTS_MY_WORK ?? "").toLowerCase()),
  "start the server with NEXT_PUBLIC_PROJECTS_MY_WORK=1",
);

/** The member's date, as the summary gives it. */
const TODAY = "2026-10-27";

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: `Task ${id}`,
    project_id: "pa",
    root_project_id: "pa",
    status_id: "a-todo",
    status_category: "todo",
    disposition: "NEXT",
    is_mine: true,
    is_triaged: true,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    assignees: ["you@example.com"],
    tags: [],
    ...over,
  };
}

/** Six tasks of mine, in three projects. */
const ROWS = [
  row("t-a", { project_id: "pa-h", due_at: "2026-10-27T10:00:00Z" }),
  row("t-b", { project_id: "pb", root_project_id: "pb", status_id: "b-todo" }),
  row("t-c", { status_id: "a-doing", status_category: "in_progress", disposition: "WAITING" }),
  row("t-d", { project_id: "pb", root_project_id: "pb", status_id: "b-back", status_category: "backlog", disposition: "SOMEDAY" }),
  row("t-e", { status_id: "a-done", status_category: "done", disposition: "DONE", completed_at: "2026-10-26T09:00:00Z" }),
  row("t-f", { project_id: "p-root", root_project_id: "p-root", disposition: "INBOX", assignees: [] }),
];

const status = (id: string, project: string, category: string, position: number, name = id) => ({
  id,
  project_id: project,
  name,
  color: "",
  position,
  category,
  is_default: false,
});

/** Ops (and its project Hiring) and Sales have their own lanes. Sales sorts first. */
const OPS = [
  status("a-back", "pa", "backlog", 10, "Backlog"),
  status("a-todo", "pa", "todo", 20, "To do"),
  status("a-doing", "pa", "in_progress", 30, "Doing"),
  status("a-review", "pa", "in_progress", 40, "Review"),
  status("a-done", "pa", "done", 50, "Done"),
];
const SALES = [
  status("b-back", "pb", "backlog", 1, "Later"),
  status("b-todo", "pb", "todo", 2, "Open"),
  status("b-doing", "pb", "in_progress", 3, "Working"),
  status("b-done", "pb", "done", 4, "Won"),
];

const SUMMARY = {
  id: "mine",
  name: "My tasks",
  level: "mine",
  tasks: 6,
  overdue: 1,
  due_soon: 2,
  due_soon_days: 7,
  by_category: { backlog: 1, todo: 3, in_progress: 1, done: 1 },
  projects: 3,
  own: { tasks: 0, overdue: 0, due_soon: 0, by_category: {} },
  children: [
    { id: "pa", name: "Ops", kind: "project", archived: false, tasks: 4, overdue: 1, by_category: { todo: 2, in_progress: 1, done: 1 } },
    { id: "pb", name: "Sales", kind: "project", archived: false, tasks: 2, overdue: 0, by_category: { todo: 1, backlog: 1 } },
  ],
  by_disposition: { inbox: 3, today: 1, next: 2, waiting: 1, someday: 1, done: 1 },
  today: TODAY,
};

const TREE = {
  rows: [
    { id: "pa", name: "Ops", tasks: 4, children: [{ id: "pa-h", name: "Hiring", parent_project_id: "pa", tasks: 1 }] },
    { id: "pb", name: "Sales", tasks: 2 },
  ],
  total: 2,
  personal: [{ id: "p-root", name: "Personal space", personal: true, can_delete: false, can_write_grants: false }],
};

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You", features: ["tasks", "projects"] },
  "projects/tree": TREE,
  "projects/my/summary": SUMMARY,
  "my/today": { today: TODAY, timezone: "UTC", stored: true, valid: true },
  "my/inbox": { rows: [], total: 0 },
  "projects/tasks": { rows: ROWS, total: ROWS.length },
  "projects/calendar": { from: "2026-10-01", to: "2026-11-01", rows: ROWS, links: [], truncated: false, cap: 500, undated: 0, unscheduled: [] },
  "projects/nodes/pa/statuses": { rows: OPS, total: OPS.length, counts: {}, owner_id: "pa" },
  "projects/nodes/pa-h/statuses": { rows: OPS, total: OPS.length, counts: {}, owner_id: "pa" },
  "projects/nodes/pb/statuses": { rows: SALES, total: SALES.length, counts: {}, owner_id: "pb" },
  "projects/needs-attention": { rows: [], total: 0, counts: {} },
};
const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

/** Every status write of a task, in order: `[task id, body]`. */
type Patch = [string, Record<string, unknown>];

async function mock(page: Page, opts: { shell?: boolean } = {}): Promise<{ patches: Patch[]; reads: string[] }> {
  const patches: Patch[] = [];
  const reads: string[] = [];
  if (opts.shell) {
    await page.addInitScript(() => {
      for (const key of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) localStorage.setItem(key, "1");
    });
  }
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\//, "");
    if (req.method() === "PATCH") {
      const id = /projects\/tasks\/([^/]+)$/.exec(path)?.[1];
      if (id) patches.push([id, req.postDataJSON() as Record<string, unknown>]);
      return route.fulfill({ json: { ...row(id ?? "x"), ...(req.postDataJSON() as object) } });
    }
    if (path.startsWith("projects/tasks") || path.startsWith("projects/my/summary")) reads.push(`${path}${url.search}`);
    // The analytics answer nothing here: each panel then draws nothing.
    if (path.includes("projects/analytics")) return route.fulfill({ status: 404, json: { detail: "not stubbed" } });
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });
  return { patches, reads };
}

const space = (page: Page) => page.getByTestId("my-tasks-space");
const chip = (page: Page, id: string) => page.locator(`[data-chip="${id}"]`);

test("the section shows My task inbox and My tasks, with the server counts", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects");
  await expect(page.getByText("My work", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  // The inbox shows `by_disposition.inbox`, and My tasks the open tasks (6 - 1 done).
  await expect(page.getByRole("button", { name: /^My task inbox\s*3$/ })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^My tasks\s*5$/ })).toBeVisible();
  // The five old rows are gone from the sidebar.
  for (const name of ["Today", "Waiting For", "Someday / Maybe"]) {
    await expect(page.getByRole("navigation").getByRole("button", { name: new RegExp(`^${name}`) })).toHaveCount(0);
  }
});

test("/projects lands on My tasks with Today, and the tab strip is a project's", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  const { reads } = await mock(page);
  await page.goto("/projects");
  await expect(space(page)).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/app=my-tasks&chip=today/);
  await expect(chip(page, "today")).toHaveAttribute("aria-pressed", "true");
  const tabs = page.locator("[data-my-tasks-tabs]");
  for (const name of ["Overview", "Board", "List", "Table", "Calendar"]) {
    await expect(tabs.getByRole("button", { name, exact: true })).toBeVisible();
  }
  await expect(tabs.getByRole("button", { name: /Needs attention/ })).toBeVisible();
  await expect(tabs.getByRole("button", { name: "Reports" })).toBeVisible();
  // Today holds the one task of mine due today.
  await expect(space(page).getByText("Task t-a", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(space(page).getByText("Task t-b", { exact: true })).toHaveCount(0);
  // The list pages through the board read over mine, and never the whole inbox.
  expect(reads.some((r) => r.includes("scope=mine") && r.includes("with_overlay=1"))).toBe(true);
});

test("the chips filter the list, and the address keeps the chip", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects?app=my-tasks");
  await expect(space(page)).toBeVisible({ timeout: 30_000 });
  // All: every row but none in TRASH. The counts come from the summary.
  await expect(space(page).getByText("Task t-b", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(chip(page, "waiting")).toContainText("1");
  await chip(page, "waiting").click();
  await expect(page).toHaveURL(/app=my-tasks&chip=waiting/);
  await expect(space(page).getByText("Task t-c", { exact: true })).toBeVisible();
  await expect(space(page).getByText("Task t-b", { exact: true })).toHaveCount(0);
  await chip(page, "next").click();
  await expect(page).toHaveURL(/app=my-tasks&chip=next/);
  await expect(space(page).getByText("Task t-a", { exact: true })).toBeVisible();
  await expect(space(page).getByText("Task t-b", { exact: true })).toBeVisible();
  await expect(space(page).getByText("Task t-c", { exact: true })).toHaveCount(0);
  // Each row names its project, with the path.
  await expect(space(page).locator("[data-project-chip]").filter({ hasText: "Ops › Hiring" })).toBeVisible();
  await page.reload();
  await expect(chip(page, "next")).toHaveAttribute("aria-pressed", "true", { timeout: 30_000 });
});

test("the Overview shows the KPI figures of the summary", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects?app=my-tasks");
  await page.locator("[data-my-tasks-tabs]").getByRole("button", { name: "Overview", exact: true }).click();
  const kpis = page.locator("[data-mine-kpis]");
  await expect(kpis).toBeVisible({ timeout: 30_000 });
  await expect(kpis).toContainText("Open");
  await expect(kpis).toContainText("5");
  await expect(kpis).toContainText("Due soon");
  await expect(kpis).toContainText("2");
  await expect(kpis).toContainText("Overdue");
  // "Where my work sits": a row for each space.
  await expect(space(page).getByRole("button", { name: /Ops/ })).toBeVisible();
  await expect(space(page).getByRole("button", { name: /Sales/ })).toBeVisible();
});

test("the board groups by stage, and a drop writes the card's own project's status", async ({ page }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const { patches } = await mock(page);
  await page.goto("/projects?app=my-tasks");
  await page.locator("[data-my-tasks-tabs]").getByRole("button", { name: "Board", exact: true }).click();
  for (const stage of ["Backlog", "To do", "In progress", "Done"]) {
    await expect(page.getByRole("group", { name: stage })).toBeVisible({ timeout: 30_000 });
  }
  const card = page.getByRole("group", { name: "To do" }).getByText("Task t-a", { exact: true });
  await expect(card).toBeVisible();
  await card.dragTo(page.getByRole("group", { name: "In progress" }));
  // Hiring uses Ops' lanes, so the first In progress status is Ops' "Doing",
  // never Sales' "Working", which sorts first in the merged set.
  await expect.poll(() => patches, { timeout: 15_000 }).toContainEqual(["t-a", { status_id: "a-doing" }]);
});

test("the old ids open My tasks with their chip", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects?app=my-today");
  await expect(page).toHaveURL(/app=my-tasks&chip=today/, { timeout: 30_000 });
  await expect(chip(page, "today")).toHaveAttribute("aria-pressed", "true");
  await page.goto("/projects?app=my-waiting");
  await expect(page).toHaveURL(/app=my-tasks&chip=waiting/, { timeout: 30_000 });
  await expect(chip(page, "waiting")).toHaveAttribute("aria-pressed", "true");
  await page.goto("/tasks");
  await expect(page).toHaveURL(/\/projects\?app=my-tasks&chip=today/, { timeout: 60_000 });
  await expect(space(page)).toBeVisible();
});

test("at 390 the bar reads Menu, My tasks, Capture, Search, Ask, and My tasks shows its chips", async ({ page }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page, { shell: true });
  await page.goto("/projects?project=pb");
  const bar = page.locator("nav").filter({ has: page.locator("[data-ask-tab]") }).last();
  await expect(bar.locator("[data-ask-tab]")).toBeVisible({ timeout: 60_000 });
  const labels = (await bar.locator("button").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  expect(labels).toEqual(["Menu", "My tasks", "Capture", "Search", "Ask"]);
  await bar.getByRole("button", { name: "My tasks" }).click();
  await expect(space(page)).toHaveAttribute("data-mode", "list", { timeout: 30_000 });
  await expect(page.locator("[data-my-tasks-chips]")).toBeVisible();
  await expect(chip(page, "all")).toBeVisible();
});

/**
 * WS-64 OP-3 — the Personal section. The tree answer carries the caller's own
 * tree in `personal`, and a team node kept under the caller's root in `rows`.
 * Playwright runs the last registered route first, so this overrides `mock`.
 */
async function mockTree(page: Page) {
  const personalArea = {
    id: "p-area",
    name: "Home admin",
    kind: "folder",
    parent_project_id: "p-root",
    personal: true,
    can_delete: true,
    can_write_grants: false,
    tasks: 2,
  };
  const personal = [
    {
      id: "p-root",
      name: "Personal space",
      personal: true,
      can_delete: false,
      can_write_grants: false,
      tasks: 2,
      children: [personalArea],
    },
  ];
  const rows = [
    { id: "t-space", name: "Team space", tasks: 1 },
    // The server (`tree.py` `_nest`) makes a team node whose parent is the
    // personal root a ROOT of `rows`.
    { id: "t-kept", name: "Kept team project", parent_project_id: "p-root" },
  ];
  await page.route("**/api/**/projects/tree", (route) =>
    route.fulfill({ json: { rows, total: 1, personal } }),
  );
}

test("the Personal section sits between My work and Spaces, with a lock", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await mockTree(page);
  await page.goto("/projects");
  const section = page.getByTestId("personal-section");
  await expect(section).toBeVisible({ timeout: 30_000 });
  await expect(section.getByText("Personal", { exact: true }).first()).toBeVisible();
  await expect(section.getByText("Personal space")).toBeVisible();
  // The team node kept under the caller's root is a root of Spaces: visible
  // outside the personal section, and absent inside it.
  await expect(section.getByText("Kept team project")).toHaveCount(0);
  await expect(page.getByText("Kept team project", { exact: true })).toBeVisible();
  await expect(page.getByText("Team space", { exact: true }).first()).toBeVisible();
  // The personal root's menu has no Move and no Archive.
  await section.getByText("Personal space").click({ button: "right" });
  const names = await page.getByRole("menuitem").allTextContents();
  expect(names.length).toBeGreaterThan(0);
  expect(names.join("|")).not.toMatch(/move|archive|share|access/i);
});
