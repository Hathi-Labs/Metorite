import { expect, test } from "@playwright/test";

/**
 * The timeline scrolls itself while you drag near its edge.
 *
 * Owner request, 2026-09-21: *"moving a task within the timeline should
 * automatically scroll left and right when we are dragging"*.
 *
 * ## Why this file exists when `edgeScroll.test.ts` already passes
 *
 * That suite checks the arithmetic — which way, how fast, what happens at the
 * clamp. It cannot check the two things that actually decide whether the
 * feature works, because `vitest.config.ts` is `environment: "node"`:
 *
 * 1. **That anything scrolls at all.** The loop, the element and the
 *    `requestAnimationFrame` are DOM.
 * 2. **That the dragged bar comes WITH the chart.** This is the real trap. A
 *    bar's position is `dayStep(clientX - originX)`, a pure pointer delta.
 *    Scroll the content under a stationary cursor and that delta does not
 *    change — so the chart slides away and the bar stays behind, reading as
 *    the bar sliding backwards out of your grip. The second test below is the
 *    only thing in the repo that can see it.
 *
 * ⚠️ **H-27: nothing in CI runs `e2e/`, so this file is not a gate.** It is a
 * real, runnable check —
 *
 *     npx playwright test e2e/projects-timeline-autoscroll.spec.ts --project=chromium
 *
 * — and saying that plainly beats calling it a fence it is not (R7).
 */

const STATUSES = [
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: true },
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

/**
 * ⚠️ Dates are relative to TODAY, and that is not tidiness.
 *
 * The chart opens centred on today rather than on the start of its range.
 * Fixed dates in the past put every bar off the left of the visible window,
 * where `boundingBox()` still returns coordinates — so the test mouses down
 * on empty space, no drag starts, and both assertions pass for the wrong
 * reason. Measured here on 2026-09-21, after the first version did exactly
 * that.
 *
 * `t1` sits just after today so it is on screen at once. `t3` is a hundred
 * days out, so the range is months wide and there is somewhere to scroll to.
 */
const DAY = 86_400_000;
const on = (offset: number) =>
  new Date(Date.now() + offset * DAY).toISOString().slice(0, 10);

const TASKS = [
  { ...base, id: "t1", task_number: 1, title: "Bootloader spike", start_date: on(1), due_at: `${on(5)}T00:00:00Z` },
  { ...base, id: "t2", task_number: 2, title: "AI Credit Assignment", start_date: on(8), due_at: `${on(12)}T00:00:00Z` },
  { ...base, id: "t3", task_number: 3, title: "Ship it", start_date: on(100), due_at: `${on(110)}T00:00:00Z` },
];

const TREE = [
  {
    id: "p1",
    name: "Firmware",
    parent_project_id: null,
    kind: "project",
    children: [{ id: "p2", name: "Bootloader", parent_project_id: "p1", kind: "project", children: [] }],
  },
];

/**
 * ⚠️ `unscheduled`, `undated` and `truncated` are here because the
 * timeline SPREADS them. A stub that omits a key the page spreads blanks
 * the page, and that reads as a broken rig rather than as a missing stub.
 */
const EMPTY = {
  rows: [], items: [], total: 0, links: [], subtasks: [], children: [],
  buckets: [], unscheduled: [], undated: 0, truncated: false,
};

async function openTimeline(page: import("@playwright/test").Page) {
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    if (p.includes("auth/me")) return route.fulfill({ json: { email: "you@example.com", name: "You" } });
    if (p.includes("projects/tree")) return route.fulfill({ json: { rows: TREE, total: 1 } });
    if (p.includes("/statuses")) return route.fulfill({ json: { rows: STATUSES, total: 1 } });
    if (p.includes("people/names")) return route.fulfill({ json: { names: {} } });
    // ⚠️ The timeline reads `projects/calendar`, NOT `projects/tasks`. It
    // draws a WINDOW of time, so it asks the endpoint that takes `from`/`to`
    // — stubbing the task list alone leaves the chart empty and reads as a
    // broken chart rather than as a missing stub.
    if (p.includes("projects/calendar"))
      return route.fulfill({
        json: { rows: TASKS, links: [], undated: 0, unscheduled: [], truncated: false },
      });
    if (p.includes("projects/tasks"))
      return route.fulfill({ json: { rows: TASKS, total: TASKS.length } });
    return route.fulfill({ json: EMPTY });
  });

  await page.goto("/projects");
  await page.waitForTimeout(3500);
  await page.getByText("Bootloader").first().click();
  await page.waitForTimeout(2500);
  await page.getByRole("button", { name: "Timeline" }).first().click();
  await page.waitForTimeout(2500);
}

/** The chart's scroll container — found by behaviour, not by a class name. */
const SCROLLER = () => {
  const all = Array.from(document.querySelectorAll("div"));
  const hit = all.find(
    (el) => el.scrollWidth > el.clientWidth + 50 && getComputedStyle(el).overflow !== "visible",
  );
  return hit ?? null;
};

test("holding a bar at the right edge scrolls the chart", async ({ page }) => {
  test.setTimeout(120_000);
  await openTimeline(page);

  const geometry = await page.evaluate((fn) => {
    const el = new Function(`return (${fn})()`)() as HTMLElement | null;
    if (!el) return null;
    const box = el.getBoundingClientRect();
    return { scrollLeft: el.scrollLeft, right: box.right, top: box.top, bottom: box.bottom };
  }, SCROLLER.toString());
  expect(geometry, "the chart has a horizontal scroll container").not.toBeNull();

  // ⚠️ `[data-bar]`, not the task NAME. The name is also the row label in
  // the sticky column, so a by-name query grabs whichever is first in the
  // DOM — the label — and the drag never starts. Both assertions below then
  // pass for the wrong reason, because a bar that is not moving is also not
  // moving wrongly. Measured here on 2026-09-21.
  const bar = page.locator('[data-bar="t1"]');
  const barBox = await bar.boundingBox();
  expect(barBox, "the bar is on screen").not.toBeNull();
  // ⚠️ INSIDE the chart, not merely present in the DOM. A bar scrolled
  // out of the window still reports a box, and mousing down on it lands on
  // nothing at all.
  expect(barBox!.x).toBeGreaterThan(geometry!.right - 700);
  expect(barBox!.x).toBeLessThan(geometry!.right - 100);

  // Grab the bar and park the cursor inside the right-hand edge zone, then
  // STOP MOVING. Everything after this point is the feature: no further
  // pointer input, and the chart must keep travelling on its own.
  await page.mouse.move(barBox!.x + barBox!.width / 2, barBox!.y + barBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(geometry!.right - 20, barBox!.y + barBox!.height / 2, { steps: 10 });
  // ⚠️ Prove the DRAG started before measuring anything. Without this the
  // test asserts that a chart nobody is dragging does not scroll, and
  // passes.
  await expect(bar).toHaveClass(/cursor-grabbing/);
  const afterMove = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );

  await page.waitForTimeout(900);
  const afterHold = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );
  await page.mouse.up();

  // Nine hundred milliseconds inside the edge zone. Even well short of top
  // speed that is hundreds of pixels, so the threshold is deliberately loose
  // — this asserts "it moved, and not by a rounding error".
  expect(afterHold - afterMove).toBeGreaterThan(150);
});

test("the dragged bar travels WITH the chart, not against it", async ({ page }) => {
  test.setTimeout(120_000);
  await openTimeline(page);

  const geometry = await page.evaluate((fn) => {
    const el = new Function(`return (${fn})()`)() as HTMLElement | null;
    if (!el) return null;
    const box = el.getBoundingClientRect();
    return { right: box.right };
  }, SCROLLER.toString());

  const bar = page.locator('[data-bar="t1"]');
  const barBox = await bar.boundingBox();

  await page.mouse.move(barBox!.x + barBox!.width / 2, barBox!.y + barBox!.height / 2);
  await page.mouse.down();
  const holdY = barBox!.y + barBox!.height / 2;
  await page.mouse.move(geometry!.right - 20, holdY, { steps: 10 });
  await expect(bar).toHaveClass(/cursor-grabbing/);
  await page.waitForTimeout(200);

  const parked = await bar.boundingBox();
  await page.waitForTimeout(900);
  const later = await bar.boundingBox();
  await page.mouse.up();

  // 🔴 THE assertion. The cursor did not move between these two reads, and the
  // chart scrolled several hundred pixels underneath it. If the drag's origin
  // follows the scroll, the bar stays put on screen — it is still under the
  // cursor. If it does not, the bar is carried left with the content by the
  // full scrolled distance, which is what "the bar slid out of my grip" looks
  // like. A generous 60px tolerance covers day-snapping.
  expect(Math.abs(later!.x - parked!.x)).toBeLessThan(60);
});
