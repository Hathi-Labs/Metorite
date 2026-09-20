import { expect, test } from "@playwright/test";

/**
 * A dropdown inside a dialog is not clipped by the dialog.
 *
 * ## The defect this exists for
 *
 * `SelectButton` drew its list with `position: absolute`. An absolutely
 * positioned box is clipped by the nearest ancestor that is not
 * `overflow: visible`, and `Modal.tsx`'s body is `overflow-hidden`. So inside
 * a dialog the control rendered a list with its bottom cut off.
 *
 * Measured in `MoveTasksDialog` on 2026-09-20, against the local stack: SIX
 * options in the DOM, a 154px panel, and two of them visible. The member saw
 * a project picker that could not reach the project they wanted.
 *
 * ⚠️ **It is invisible to every other kind of test.** The options are all
 * present in the DOM and all have correct text, so a query-by-role test
 * passes. `vitest` is `environment: "node"` and has no layout at all. Only a
 * real browser, asking what CLIPS the panel, can see it.
 *
 * ⚠️ **And it is a regression waiting to happen.** A portal looks like
 * unnecessary machinery next to `absolute left-0 top-full`, which is what the
 * control had for a month. This test is the thing that argues back.
 *
 * ⚠️ H-27: nothing in CI runs `e2e/`, so this is a runnable check and not yet
 * a gate. Run it with:
 *
 *     npx playwright test e2e/projects-select-portal.spec.ts --project=chromium
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

/** Deep enough that a clipped panel loses real rows, like the reported one. */
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

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You" },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
  "projects/tasks": { rows: [TASK], total: 1 },
};

/** The first ancestor that would clip an absolutely positioned child. */
const CLIPPER = (el: Element) => {
  let node = el.parentElement;
  while (node && node !== document.body) {
    const style = getComputedStyle(node);
    if (style.overflow !== "visible" || style.overflowY !== "visible") {
      return node.className.slice(0, 60);
    }
    node = node.parentElement;
  }
  return null;
};

test("the Move dialog's project picker is not clipped by the dialog", async ({
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
  await page.waitForTimeout(1500);

  await page.getByRole("button", { name: "Move to", exact: true }).click();
  await page.waitForTimeout(700);

  const panel = page.getByRole("listbox");
  await expect(panel).toBeVisible();

  expect(
    await panel.evaluate(CLIPPER),
    "the picker's panel sits inside a clipping ancestor. `Modal`'s body is " +
      "`overflow-hidden`, so an absolutely positioned list is cut off — the " +
      "panel must be portalled. See this file's header.",
  ).toBeNull();

  // Every row the tree produced must be reachable, not merely present.
  const options = page.getByRole("option");
  await expect(options).toHaveCount(6);
  for (const option of await options.all()) {
    await expect(
      option,
      "an option is in the DOM but not on screen, which is what clipping " +
        "looks like from the outside.",
    ).toBeInViewport();
  }

  // The folder is offered and refused, with its reason — dropping it would
  // flatten the tree and lose what explains the indent beneath it.
  await expect(page.getByRole("option", { name: /APP\.METORITE/ })).toBeDisabled();
});
