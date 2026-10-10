import { expect, test } from "@playwright/test";

/**
 * The Move dialog's project picker: every row reachable, and the tree usable.
 *
 * ## Why this file exists
 *
 * It began as `projects-select-portal.spec.ts`. `SelectButton` drew its list
 * `position: absolute` inside `Modal`'s `overflow-hidden` body, so the list
 * was cut off — measured on 2026-09-20: SIX options in the DOM and two on
 * screen. The fix portalled the list.
 *
 * On 2026-10-10 the owner found the list itself hard to read: every node of
 * every space, open, with an excuse on each folder. The dialog now draws
 * `ProjectPicker` INLINE — a search box over a tree whose spaces start closed
 * — so there is no dropdown left to clip. The claim that survives is the one
 * that mattered: **every row the tree produces is on screen, not merely in
 * the DOM.** Only a real browser can see that, because the rows are all
 * present either way.
 *
 * The portal fence for `SelectButton` itself lives with `AnchoredPanel`.
 *
 *     npx playwright test e2e/projects-move-picker.spec.ts --project=chromium
 */

const STATUSES = [
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: false },
  { id: "s4", project_id: "p2", name: "Done", color: "green", position: 40, category: "done", is_default: false },
];

const TASK = {
  id: "t2",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 2,
  status_id: "s2",
  title: "Notification engine for projects",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

/** A space, a project, a folder and two projects in it — the shape reported. */
const TREE = [
  {
    id: "p1",
    name: "Hathi Labs Projects",
    parent_project_id: null,
    kind: "project",
    children: [
      {
        id: "p2",
        name: "Metorite",
        parent_project_id: "p1",
        kind: "project",
        children: [
          {
            id: "p3",
            name: "APP.METORITE",
            parent_project_id: "p2",
            kind: "folder",
            children: [
              { id: "p4", name: "Bugs", parent_project_id: "p3", kind: "project", children: [] },
              { id: "p5", name: "Projects Tasks App", parent_project_id: "p3", kind: "project", children: [] },
            ],
          },
        ],
      },
    ],
  },
];

/** Everything `MoveTasksDialog` reads off a preview. A missing key is a white page. */
const PREVIEW = {
  source_project_id: "p2",
  destination_project_id: "p4",
  crosses_status_set: false,
  crosses_root: false,
  statuses: [],
  destination_statuses: [],
  status_map: {},
  drops: {},
  types: [],
  tags: { unregistered: [] },
  field_map: {},
  orphan_fields: [],
  moved: 1,
  dropped_fields: [],
};

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You" },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
  "projects/tasks": { rows: [TASK], total: 1 },
  "tasks/move/preview": PREVIEW,
};

test("the Move dialog's project picker shows every row, searches, and folds", async ({
  page,
}) => {
  test.setTimeout(120_000);

  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });

  await page.goto("/projects");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(4000);
  await page.getByText("Metorite", { exact: true }).last().click();
  await page.waitForTimeout(2500);

  // Select a task, so the bulk bar offers the move.
  await page.getByText("Notification engine for projects").first().hover();
  await page.waitForTimeout(400);
  await page
    .locator("li")
    .filter({ hasText: "Notification engine for projects" })
    .last()
    .getByRole("button", { name: "Select", exact: true })
    .click();
  await page.waitForTimeout(700);

  await page.getByRole("button", { name: /Move to project/ }).first().click();

  // The search box takes the caret: most picks are a name already known.
  const search = page.getByRole("combobox", { name: /Move to/ });
  await expect(search).toBeFocused({ timeout: 10_000 });

  // A short tree starts fully open, and every row is ON SCREEN, not merely in
  // the DOM — which is what clipping looks like from the outside.
  const rows = page.getByRole("treeitem");
  await expect(rows).toHaveCount(5);
  for (const row of await rows.all()) {
    await expect(row, "a row is in the DOM but not on screen").toBeInViewport();
  }

  // A folder is a header that OPENS, never a disabled row with an excuse.
  const folder = page.getByRole("treeitem", { name: /APP\.METORITE/ });
  await expect(folder).toHaveAttribute("aria-expanded", "true");
  await expect(folder).not.toHaveAttribute("aria-disabled", "true");
  await folder.click();
  await expect(folder).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("treeitem", { name: /Bugs/ })).toHaveCount(0);

  // A search reaches into the closed folder, and names the path it found.
  await search.fill("bugs");
  const hit = page.getByRole("treeitem", { name: /Bugs/ });
  await expect(hit).toHaveCount(1);
  await expect(hit).toContainText("Hathi Labs Projects › Metorite › APP.METORITE");

  // Enter takes the top match, and the list folds to the breadcrumb.
  await search.press("Enter");
  await expect(page.getByRole("button", { name: "Change", exact: true })).toBeVisible();
  await expect(page.getByRole("treeitem")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Move", exact: true }).last()).toBeEnabled({
    timeout: 10_000,
  });
});
