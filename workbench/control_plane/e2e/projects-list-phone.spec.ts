import { expect, test, type Page } from "@playwright/test";

/**
 * MOB-2 — the phone row of the Projects List (`one_projects_app.md` §9b.4,
 * task 1537).
 *
 * ## The rule
 *
 * At 390 x 844 and 360 x 780, on a touch phone, every List row shows all five
 * parts (tick, title, project, due, status). Neither the page nor the list has a
 * sideways scroll. One tap on the tick finishes the task.
 *
 * ## How to run it
 *
 * The compact chrome is a build flag, so the dev server must start with it:
 *
 *     NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1 npx playwright test \
 *       e2e/projects-list-phone.spec.ts
 *
 * Without the flag the file skips, because the phone row ships with the chrome.
 *
 * ## Mutations (run by hand, 2026-11-06)
 *
 * - Put `min-w-[640px]` back on the phone list: the "no sideways scroll" test
 *   fails at 360.
 * - Remove the `onClick` of `data-phone-tick` in `TaskList.tsx`: the "one tap"
 *   test fails (no PATCH is sent).
 * - Drop the due chip from `phoneRowParts`: the "five parts" test fails.
 */

test.skip(
  !["1", "true", "on", "yes"].includes(
    (process.env.NEXT_PUBLIC_PROJECTS_COMPACT_CHROME ?? "").toLowerCase(),
  ),
  "start the server with NEXT_PUBLIC_PROJECTS_COMPACT_CHROME=1",
);

const STATUSES = ["Backlog", "To do", "Spec ready", "Done"].map((name, i) => ({
  id: `s${i}`,
  project_id: "p2",
  name,
  color: "gray",
  position: (i + 1) * 10,
  category: i === 3 ? "done" : i === 0 ? "backlog" : "todo",
  is_default: i === 0,
}));

const inDays = (d: number) => new Date(Date.now() + d * 86_400_000).toISOString();

const task = (i: number, status = "s2") => ({
  id: `t${i}`,
  project_id: "p2",
  root_project_id: "p1",
  task_number: 140 + i,
  status_id: status,
  title: `Task number ${i} with a title long enough to need a second line on a phone`,
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
  due_at: inDays(3 + i),
  completed_at: null,
});

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

async function openList(page: Page, patches: { id: string; body: unknown }[]) {
  const tasks = Array.from({ length: 6 }, (_, i) => task(i + 1));
  const fixtures: Record<string, unknown> = {
    "auth/me": { email: "you@example.com", name: "You" },
    "projects/tree": { rows: TREE, total: 1 },
    "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
    "projects/tasks": { rows: tasks, total: tasks.length },
  };
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\//, "");
    if (req.method() === "PATCH" && /tasks\/t\d+$/.test(path)) {
      const body = JSON.parse(req.postData() ?? "{}");
      patches.push({ id: path.split("/").pop()!, body });
      return route.fulfill({
        json: { ...tasks[0], id: path.split("/").pop(), ...body, completed_at: inDays(0) },
      });
    }
    const hit = Object.keys(fixtures)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (fixtures[hit] as object) : EMPTY });
  });
  await page.goto("/projects?project=p2");
  await page.waitForLoadState("domcontentloaded");
  await expect(page.locator("[data-phone-row]").first()).toBeVisible({ timeout: 60_000 });
}

for (const size of [
  { width: 390, height: 844 },
  { width: 360, height: 780 },
]) {
  test.describe(`${size.width} x ${size.height}`, () => {
    test.use({ viewport: size, isMobile: true, hasTouch: true });

    test("a row shows all five parts and nothing scrolls sideways", async ({ page }) => {
      test.setTimeout(120_000);
      await openList(page, []);
      const row = page.locator("[data-phone-row]").first();
      await expect(row.getByRole("button", { name: /^Mark Task number 1 .* done$/ })).toBeVisible();
      await expect(row.getByText(/^Task number 1 with/)).toBeVisible();
      await expect(row.locator("[data-project-chip]")).toContainText("Bootloader");
      await expect(row.locator("[data-phone-due]")).toBeVisible();
      await expect(row.locator("[data-phone-status]")).toContainText("Spec ready");
      // No `#` column on the phone.
      await expect(row).not.toContainText("#141");

      const tick = await row.locator("[data-phone-tick]").boundingBox();
      expect(tick!.width).toBeGreaterThanOrEqual(40);
      expect(tick!.height).toBeGreaterThanOrEqual(40);

      const over = await page.evaluate(() => {
        const page = document.scrollingElement!;
        const list = document.querySelector<HTMLElement>("[data-list-phone]")!;
        let scroller: HTMLElement | null = list;
        const wide: string[] = [];
        while (scroller) {
          if (scroller.scrollWidth > scroller.clientWidth + 1)
            wide.push(`${scroller.tagName}.${scroller.className.slice(0, 40)}`);
          scroller = scroller.parentElement;
        }
        return {
          page: page.scrollWidth - page.clientWidth,
          wide,
          rowsFit: [...document.querySelectorAll<HTMLElement>("[data-phone-row]")].every(
            (r) => r.getBoundingClientRect().right <= window.innerWidth + 1,
          ),
        };
      });
      expect(over.page, "the page scrolls sideways").toBeLessThanOrEqual(0);
      expect(over.wide, "an ancestor of the list is wider than its box").toEqual([]);
      expect(over.rowsFit).toBe(true);
    });

    test("one tap on the tick finishes the task", async ({ page }) => {
      test.setTimeout(120_000);
      const patches: { id: string; body: unknown }[] = [];
      await openList(page, patches);
      await page
        .getByRole("button", { name: /^Mark Task number 1 .* done$/ })
        .tap();
      await expect.poll(() => patches.length, { timeout: 15_000 }).toBe(1);
      expect(patches[0]).toEqual({ id: "t1", body: { status_id: "s3" } });
      // The tick did not open the task.
      await expect(page.getByText("Your focus")).toHaveCount(0);
    });

    test("a tap on the row opens the task", async ({ page }) => {
      test.setTimeout(120_000);
      await openList(page, []);
      await page.locator("[data-phone-row]").nth(1).locator("[data-phone-status]").tap();
      // The panel is full screen on a phone: its Status field and #142 show.
      await expect(page.getByText("Your focus")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText("#142").first()).toBeVisible();
    });
  });
}
