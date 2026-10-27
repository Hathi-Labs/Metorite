import { expect, test, type Page } from "@playwright/test";

/**
 * The phone chrome of Projects (WS-64 OP-1, `one_projects_app.md` §5.1, §9).
 *
 * ## The rule
 *
 * On a phone, the first task row shows in the top third of the screen. At
 * 390 × 844 the top third ends at y = 281, so the top of the first row must be
 * at or above 281 with no filter on.
 *
 * ## How to run it
 *
 * The flag is inlined at build time, so the dev server must start with it:
 *
 *     NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1 npx playwright test \
 *       e2e/projects-compact-chrome.spec.ts
 *
 * The suite's `webServer` inherits this environment. Without the flag the file
 * skips, because the old chrome is meant to be taller than a third.
 *
 * A vitest test cannot see layout, which is why this measures it
 * (`src/app/projects/components/compactChrome.test.ts` fences the structure).
 */

test.skip(
  !["1", "true", "on", "yes"].includes(
    (process.env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME ?? "").toLowerCase(),
  ),
  "start the server with NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1",
);

const STATUSES = ["Backlog", "To do", "Doing", "Done"].map((name, i) => ({
  id: `s${i}`,
  project_id: "p2",
  name,
  color: "gray",
  position: (i + 1) * 10,
  category: i === 3 ? "done" : i === 0 ? "backlog" : "todo",
  is_default: i === 0,
}));

const TASKS = Array.from({ length: 12 }, (_, i) => ({
  id: `t${i}`,
  project_id: "p2",
  root_project_id: "p1",
  task_number: i + 1,
  status_id: "s0",
  title: `Task number ${i + 1}`,
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
}));

const TREE = [
  {
    id: "p1",
    name: "Firmware",
    parent_project_id: null,
    kind: "project",
    children: [
      { id: "p2", name: "Bootloader", parent_project_id: "p1", kind: "project", children: [] },
    ],
  },
];

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You" },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
  // 12 loaded of 219, so the tool row shows "12 of 219".
  "projects/tasks": { rows: TASKS, total: 219 },
};

async function openProject(page: Page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });
  await page.goto("/projects?project=p2");
  await page.waitForLoadState("domcontentloaded");
  await expect(page.getByText("Task number 1", { exact: true }).first()).toBeVisible({
    timeout: 30_000,
  });
}

test("the first task row is in the top third at 390 x 844", async ({ page }) => {
  test.setTimeout(120_000);
  await openProject(page);
  const box = await page.getByText("Task number 1", { exact: true }).first().boundingBox();
  expect(box, "the first row did not render").not.toBeNull();
  expect(
    box!.y,
    `the first task row starts at y = ${Math.round(box!.y)}. The top third ends at 281.`,
  ).toBeLessThanOrEqual(281);
});

test("the chrome is one tool row, and the controls are in the sheets", async ({ page }) => {
  test.setTimeout(120_000);
  await openProject(page);
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "View", exact: true })).toBeVisible();
  // These live in the sheets now, so none is on the page until a sheet opens.
  await expect(page.getByRole("button", { name: "Overdue", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("button", { name: "Overdue", exact: true }).click();
  await page.getByRole("button", { name: /^Show \d+ tasks?$/ }).click();
  await expect(page.getByRole("button", { name: "Filters 1", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove filter Overdue" })).toBeVisible();
});
