import { expect, test } from "@playwright/test";
import { clickAndWait, firstVisible, gotoAndSettle, readPaint, stubApi, underAccents } from "./visual/harness";

/**
 * A status colour must not move when the member changes their accent.
 *
 * ## Why this is a browser test and not a unit test
 *
 * `statusAccent.test.ts` can assert that a hue does not name `primary`. It
 * cannot assert what the browser PAINTS, and that gap is exactly where this
 * defect lived: every unit test asserted a hue NAME, and the names were right
 * the whole time. `--primary` was the accent a member picks at Settings →
 * Appearance, so a board's "In progress" lane was whatever colour the viewer
 * had chosen, and an accent set to green made an active lane the same colour
 * as Done.
 *
 * `project-state.spec.ts` warned about this class in its own header: "a colour
 * column existed for months and every lane drew the same grey, and no unit test
 * could see it." This is that warning, made into a test.
 *
 * ## What it does NOT claim
 *
 * It says nothing about whether the chosen hues are pleasant, or whether they
 * clear contrast — `src/lib/theme/contrast.ts` owns that. It claims one thing:
 * changing the accent must not change what a status means.
 */

const TREE = {
  rows: [
    {
      id: "d-1",
      name: "Delivery",
      kind: "department",
      status: "active",
      children: [{ id: "p-1", name: "Firmware", kind: "project", status: "active", children: [] }],
    },
  ],
};

const STATUSES = [
  { id: "s-todo", name: "To do", category: "todo", position: 1 },
  { id: "s-doing", name: "In progress", category: "in_progress", position: 2 },
  { id: "s-done", name: "Done", category: "done", position: 3 },
];

const TASKS = {
  rows: STATUSES.map((s, i) => ({
    id: `t-${i}`,
    title: `Task in ${s.name}`,
    project_id: "p-1",
    status_id: s.id,
    assignees: [],
    due_at: null,
    importance: 1,
    tags: [],
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-09-01T09:00:00Z",
    completed: s.category === "done",
  })),
  total: STATUSES.length,
};

/** Two accents far apart in hue. Green is the cruel one: `done` is green. */
const ACCENTS = ["hsl(210 90% 50%)", "hsl(120 60% 45%)", "hsl(280 70% 55%)"];

test("a member's accent does not repaint the board's statuses", async ({ page }) => {
  test.setTimeout(120_000);

  await stubApi(page, {
    "projects/tree": TREE,
    "projects/tasks": TASKS,
    statuses: { rows: STATUSES },
    "/summary": {
      level: "project",
      by_category: { todo: 1, in_progress: 1, done: 1 },
      overdue: 0,
      unassigned: 3,
      total: 3,
      children: [],
    },
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  // The harness helpers, not raw locators: `.first()` returns a hidden mobile
  // duplicate, and a canvas mounts with its data rather than with the click.
  await gotoAndSettle(page, "/projects");
  expect(await clickAndWait(page, "Firmware"), "could not select a project").toBe(true);

  const lane = await firstVisible(page, "In progress");
  expect(lane, "the In progress lane never rendered").not.toBeNull();

  const paints = await underAccents(page, ACCENTS, () => readPaint(lane!, "color"));

  // Every accent must give the same paint. If they differ, this status is
  // wired to `--primary` again.
  expect(new Set(paints).size, `"In progress" painted ${paints.join(", ")} under three accents`).toBe(1);
});
