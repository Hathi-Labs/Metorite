import { expect, test } from "@playwright/test";

/**
 * A focused field in the task panel shows its WHOLE focus ring.
 *
 * Owner report, 2026-10-09: the orange ring around a focused Description was
 * cut off at both ends. The folding section's panel clips (`overflow-hidden`,
 * for the fold), and `.cc-control:focus-visible` draws the ring OUTSIDE the
 * field: a 2px outline after a 2px offset. `CollapsibleSection` now leaves the
 * room (`PANEL_CLASS`), and `src/components/ui/collapsible.test.ts` holds the
 * arithmetic. This test asks the browser what really clips.
 */

const TASK = {
  id: "t2",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 2,
  status_id: "s2",
  title: "Per-person credit limits",
  description: "Allow an admin to allocate a specific number of credits to each person.",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

const STATUSES = [
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: false },
];

const TREE = [{ id: "p1", name: "Hathi Labs", parent_project_id: null, kind: "project", children: [
  { id: "p2", name: "Metorite", parent_project_id: "p1", kind: "project", children: [] },
] }];

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You", features: ["projects", "tasks"], is_admin: false },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: 1 },
  "projects/tasks/t2": TASK,
  "projects/tasks": { rows: [TASK], total: 1 },
};

/** How far the ring of a box reaches beyond it, from the CSS that draws it. */
const RING_REACH = 4;

test("the Description field's focus ring is not clipped", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.endsWith(key) || path.includes(`${key}?`));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });

  await page.goto("/projects?task=t2");
  const field = page.getByRole("textbox", { name: "Description" });
  await expect(field).toBeVisible({ timeout: 20_000 });
  await field.scrollIntoViewIfNeeded();
  await field.focus();
  await page.waitForTimeout(300);

  const clipped = await field.evaluate((el, reach) => {
    const r = el.getBoundingClientRect();
    const ring = { left: r.left - reach, right: r.right + reach, top: r.top - reach, bottom: r.bottom + reach };
    const cuts: string[] = [];
    let node = el.parentElement;
    while (node && node !== document.body) {
      const s = getComputedStyle(node);
      if (s.overflowX !== "visible" || s.overflowY !== "visible") {
        const c = node.getBoundingClientRect();
        if (ring.left < c.left - 0.5) cuts.push(`left by ${(c.left - ring.left).toFixed(1)}px in ${node.className.slice(0, 50)}`);
        if (ring.right > c.right + 0.5) cuts.push(`right by ${(ring.right - c.right).toFixed(1)}px in ${node.className.slice(0, 50)}`);
        if (ring.bottom > c.bottom + 0.5 && s.overflowY !== "auto" && s.overflowY !== "scroll") {
          cuts.push(`bottom by ${(ring.bottom - c.bottom).toFixed(1)}px in ${node.className.slice(0, 50)}`);
        }
      }
      node = node.parentElement;
    }
    return cuts;
  }, RING_REACH);

  await page.screenshot({ path: "test-results/projects-focus-ring.png" });
  expect(clipped, "the ring is cut off by a clipping ancestor").toEqual([]);
});
