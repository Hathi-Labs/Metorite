import { expect, test } from "@playwright/test";

/**
 * A write must repaint the surface the member is looking at.
 *
 * ## The two defects this pins, both reported by the owner on 2026-09-20
 *
 * > "When I do things like moving to another project, adding a tag, and any
 * > other changes that I make to a particular task, it doesn't immediately
 * > show up in the UI. I have to refresh the UI to have it show up there."
 *
 * 1. **`refreshRef` pointed at `loadMonth`.** That function reads the
 *    CALENDAR window and sets `month`. It touches neither `tasks` nor
 *    `statuses`. So every undo step, every redo, every task patch routed
 *    through `rewriteTask` and both link writes refreshed a surface nobody
 *    was looking at, while the board kept its old rows.
 * 2. **`moveTasksTo` bumped `treeKey` and nothing else.** That re-reads the
 *    TREE. The moved cards stayed on the board they had just left.
 *
 * ## And the one underneath them
 *
 * `useCachedResource` woke on `invalidate` and re-read with `force`. A forced
 * read always calls `put`, and `put` wakes every watcher of the key —
 * including the one that caused it. Measured in a browser against the local
 * stack: one write, and `/api/projects/tree` was re-read 42 times in eight
 * seconds, still going nineteen seconds later, median gap 187 ms. Per tab,
 * until the member navigated away. `src/lib/dataCache.test.ts` holds the
 * reduced case and IS a gate. The second test here is the end-to-end one.
 *
 * ⚠️ **H-27: nothing in CI runs `e2e/`, so this file is not yet a gate.** It
 * is a real, runnable check —
 *
 *     npx playwright test e2e/projects-stale-after-write.spec.ts --project=chromium
 *
 * — and naming that honestly beats calling it a fence it is not (R7).
 */

const STATUSES = [
  { id: "s1", project_id: "p2", name: "Backlog", color: "gray", position: 10, category: "backlog", is_default: false },
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: true },
  { id: "s4", project_id: "p2", name: "Done", color: "green", position: 40, category: "done", is_default: false },
];

const base = {
  project_id: "p2",
  root_project_id: "p1",
  status_id: "s2",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

const BEFORE = [
  { ...base, id: "t2", task_number: 2, title: "Notification engine for projects" },
  { ...base, id: "t3", task_number: 3, title: "Second card" },
];

/**
 * What the board holds once the move has landed: the moved card is somewhere
 * else, and the other one is renamed to a string the page never held.
 *
 * ⚠️ **The rename is what makes this a test of REFETCHING.** A card can
 * disappear because the page patched its own list optimistically. It cannot
 * grow a title nobody sent it.
 */
const AFTER = [
  { ...base, id: "t3", task_number: 3, title: "ONLY A REFETCH KNOWS THIS" },
];

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

/** Everything `MoveTasksDialog` reads. A missing key here is a white page. */
const PREVIEW = {
  source_project_id: "p2",
  destination_project_id: "p1",
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

/** Answers every call, and reports what was asked. */
async function stub(page: import("@playwright/test").Page) {
  const taskReads: number[] = [];
  const treeReads: number[] = [];
  const writes: string[] = [];
  let written = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\//, "");

    if (request.method() !== "GET") {
      writes.push(`${request.method()} ${path}`);
      if (path.includes("tasks/move/preview")) {
        // The preview the dialog reads before it offers the button.
        return route.fulfill({ json: PREVIEW });
      }
      written = true;
      return route.fulfill({ json: { moved: 1, dropped_fields: [] } });
    }
    if (path.includes("projects/tasks")) {
      taskReads.push(Date.now());
      return route.fulfill({ json: { rows: written ? AFTER : BEFORE, total: 2 } });
    }
    if (path.includes("nodes/p2/statuses")) {
      return route.fulfill({ json: { rows: STATUSES, total: STATUSES.length } });
    }
    if (path.includes("projects/tree")) {
      treeReads.push(Date.now());
      return route.fulfill({ json: { rows: TREE, total: 1 } });
    }
    if (path.includes("auth/me")) {
      return route.fulfill({ json: { email: "you@example.com", name: "You" } });
    }
    return route.fulfill({ json: EMPTY });
  });

  await page.goto("/projects");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(4000);
  // ⚠️ The CHILD. A space draws a dashboard; only a project draws the board.
  await page.getByText("Bootloader", { exact: true }).first().click();
  await page.waitForTimeout(2500);

  return { taskReads, treeReads, writes };
}

/** Select one card, then move it out of this board through the bulk bar. */
async function moveFirstCardAway(page: import("@playwright/test").Page) {
  const card = page.locator("[data-card]").filter({ hasText: "Notification engine" }).last();
  await expect(card).toHaveCount(1);
  await card.hover();
  await card.getByRole("button", { name: /Select/i }).click();
  await page.getByRole("button", { name: /Move to project/ }).first().click();
  // The picker is portalled to `document.body` (AnchoredPanel), so it is not
  // inside the dialog in the DOM even though it is drawn over it.
  await page.getByText("Pick a project", { exact: false }).first().click();
  const options = page.locator('[role="option"]');
  let destination = null;
  for (let i = 0; i < (await options.count()); i += 1) {
    if (await options.nth(i).isEnabled()) {
      const label = (await options.nth(i).innerText()).trim();
      if (label && !label.startsWith("Pick")) { destination = options.nth(i); break; }
    }
  }
  if (!destination) throw new Error("the picker offered no destination");
  await destination.click();
  // ⚠️ The portalled list is drawn OVER the dialog's own buttons. Clicking
  // "Move" while it is still mounted hits the listbox instead.
  await page.locator('[role="listbox"]').waitFor({ state: "detached", timeout: 10_000 });
  const confirm = page.getByRole("button", { name: "Move", exact: true }).last();
  await expect(confirm).toBeEnabled({ timeout: 10_000 });
  await confirm.click();
}

test("a move repaints the board, with no reload", async ({ page }) => {
  test.setTimeout(120_000);
  const { writes } = await stub(page);

  await expect(page.locator("[data-card]")).toHaveCount(2);
  await moveFirstCardAway(page);

  // The title no optimistic patch could produce. It only arrives on a re-read.
  await expect(page.getByText("ONLY A REFETCH KNOWS THIS")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-card]")).toHaveCount(1);
  expect(writes.some((w) => w.includes("tasks/move"))).toBe(true);
});

test("one write is one re-read, not a poll", async ({ page }) => {
  test.setTimeout(120_000);
  const { treeReads } = await stub(page);
  const settled = treeReads.length;

  await moveFirstCardAway(page);
  await page.waitForTimeout(9000);

  const after = treeReads.length - settled;
  // 🔴 Before the fix this was unbounded and rose for as long as the tab
  // stayed open: 42 tree reads in eight seconds, median gap 187 ms. Three
  // allows the write's own re-read plus a focus revalidation.
  expect(after, `the tree was re-read ${after} times after ONE write`).toBeLessThanOrEqual(3);
});
