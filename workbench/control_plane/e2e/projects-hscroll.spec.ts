import { expect, test, type Page } from "@playwright/test";

/**
 * The sideways scrollbar is always on screen (owner ask, 2026-09-24).
 *
 * ## The defect
 *
 * The board, the list and the table each wrapped themselves in their own
 * `overflow-x-auto`, INSIDE the page canvas that scrolls vertically. So each
 * view's horizontal scrollbar sat under its last row. With forty tasks, the
 * member had to scroll to the bottom of the page to find the scrollbar, and
 * then back up to find the row they wanted.
 *
 * ## The rule
 *
 * The page canvas (`page.tsx`, `min-h-0 flex-1 overflow-auto`) is the ONE
 * scroller for these three views, in both directions. It is as tall as the
 * window, so its scrollbar is always at the bottom edge. A view must not add a
 * horizontal scroller of its own.
 *
 * This test measures the rule, not a class name: for each view, the nearest
 * ancestor that can scroll sideways must end inside the window. A unit test
 * cannot see this, because vitest runs without layout.
 */

const COLUMNS = 10;
const STATUSES = Array.from({ length: COLUMNS }, (_, i) => ({
  id: `s${i}`,
  project_id: "p2",
  name: `Stage ${i + 1}`,
  color: "gray",
  position: (i + 1) * 10,
  category: i === COLUMNS - 1 ? "done" : "todo",
  is_default: i === 0,
}));

// Forty tasks in the FIRST column, so the board is tall AND wide.
const TASKS = Array.from({ length: 40 }, (_, i) => ({
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
  "projects/tasks": { rows: TASKS, total: TASKS.length },
};

/** Where the nearest sideways scroller of `label`'s view ends, and the page. */
async function measure(page: Page, label: string) {
  return page.evaluate((prefix) => {
    const view = [...document.querySelectorAll("[aria-label]")].find((el) =>
      (el.getAttribute("aria-label") ?? "").startsWith(prefix),
    );
    if (!view) return null;
    let el: Element | null = view;
    while (el && el !== document.body) {
      const x = getComputedStyle(el).overflowX;
      if (x === "auto" || x === "scroll") break;
      el = el.parentElement;
    }
    if (!el || el === document.body) return { found: false } as const;
    const box = el.getBoundingClientRect();
    return {
      found: true,
      bottom: Math.round(box.bottom),
      windowHeight: window.innerHeight,
      overflows: el.scrollWidth > el.clientWidth,
      pageWidth: document.documentElement.scrollWidth,
      windowWidth: window.innerWidth,
    } as const;
  }, label);
}

for (const [mode, label] of [
  ["board", "Task board"],
  ["list", "Task list"],
  ["table", "Task table"],
] as const) {
  test(`${mode}: the sideways scrollbar is on screen without scrolling down`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1280, height: 800 });
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
    // ⚠️ The CHILD. A space draws a dashboard; only a project draws the board.
    await page.getByText("Bootloader", { exact: true }).first().click();
    await page.waitForTimeout(2000);
    await page
      .getByRole("group", { name: "View mode" })
      .getByRole("button", { name: mode, exact: true })
      .first()
      .click();
    await page.waitForTimeout(2000);

    const m = await measure(page, label);
    expect(m, `the ${mode} view did not render`).not.toBeNull();
    expect(m!.found, "nothing scrolls sideways around the view").toBe(true);
    if (!m!.found) return;
    expect(
      m!.bottom,
      `the ${mode}'s sideways scroller ends at ${m!.bottom}px, below the ` +
        `${m!.windowHeight}px window, so its scrollbar is off screen. A view ` +
        "has grown its own overflow-x-auto again — see this file's header.",
    ).toBeLessThanOrEqual(m!.windowHeight);
    // The page itself must not widen: the canvas scrolls, not the window.
    expect(m!.pageWidth).toBeLessThanOrEqual(m!.windowWidth);
    // Ten 18rem columns cannot fit 1280px, so the board MUST overflow. If it
    // does not, the test measured nothing.
    if (mode === "board") expect(m!.overflows).toBe(true);
  });
}
