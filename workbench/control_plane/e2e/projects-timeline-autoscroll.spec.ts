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
  // ⚠️ Filler, so the chart is TALLER than its container. Without it the
  // vertical axis cannot scroll at all, and the two tests about vertical
  // behaviour would pass against any implementation whatsoever.
  ...Array.from({ length: 24 }, (_, i) => ({
    ...base,
    id: `f${i}`,
    task_number: 10 + i,
    title: `Filler ${i}`,
    start_date: on(2 + (i % 20)),
    due_at: `${on(6 + (i % 20))}T00:00:00Z`,
  })),
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

async function openTimeline(
  page: import("@playwright/test").Page,
  writes?: string[],
) {
  await page.route("**/api/**", async (route) => {
    const method = route.request().method();
    if (method !== "GET") {
      writes?.push(`${method} ${new URL(route.request().url()).pathname}`);
      return route.fulfill({ json: { id: "t1" } });
    }
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
    // ⚠️ The SINGLE-task read first. `projects/tasks` also matches
    // `projects/tasks/t1`, and answering that with a list shape leaves the
    // panel with no task to draw — which reads as "the click did not open
    // it", the very thing the third test is asserting about.
    const one = p.match(/projects\/tasks\/([^/?]+)$/);
    if (one) {
      const found = TASKS.find((t) => t.id === one[1]);
      if (found) return route.fulfill({ json: found });
    }
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
  const before = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );
  await page.waitForTimeout(900);
  const later = await bar.boundingBox();
  const after = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );
  await page.mouse.up();

  // 🔴 THIS LINE IS WHY THE TEST BELOW MEANS ANYTHING, and it was missing.
  //
  // The assertion after it says the bar did not move on screen. With the
  // whole feature removed the chart does not scroll, so the bar does not move
  // either, and `Math.abs(0)` sails under the tolerance — the test reports
  // green for exactly the case it exists to catch. Verified on 2026-09-21 by
  // deleting the feature and watching it pass.
  //
  // A test whose subject did not happen is not a passing test.
  expect(after - before, "the chart scrolled, so the next line has a subject")
    .toBeGreaterThan(150);

  // 🔴 THE assertion. The cursor did not move between these two reads, and the
  // chart scrolled several hundred pixels underneath it. If the drag's origin
  // follows the scroll, the bar stays put on screen — it is still under the
  // cursor. If it does not, the bar is carried left with the content by the
  // full scrolled distance, which is what "the bar slid out of my grip" looks
  // like. A generous 60px tolerance covers day-snapping.
  expect(Math.abs(later!.x - parked!.x)).toBeLessThan(60);
});


test("a CLICK on a bar near the edge opens it and changes no dates", async ({ page }) => {
  test.setTimeout(120_000);

  // 🔴 THE regression this feature shipped in its first draft, found in
  // review. Press a bar that already sits inside the edge zone and the loop
  // starts at once: the chart scrolls ~47px during a 100ms press, the drag's
  // origin follows it, `dayStep` reports two whole days at the month zoom and
  // seven at the quarter zoom — so the press PATCHes a new date, and because
  // the component reads "did it travel" to tell a click from a drag, the task
  // does not open either. A task that refuses to open and quietly moves.
  //
  // `DRAG_SLOP` is the fix. This is what proves it.
  const writes: string[] = [];
  await openTimeline(page, writes);

  const scroller = await page.evaluate((fn) => {
    const el = new Function(`return (${fn})()`)() as HTMLElement;
    return { right: el.getBoundingClientRect().right, scrollLeft: el.scrollLeft };
  }, SCROLLER.toString());

  const bar = page.locator('[data-bar="t1"]');
  const first = await bar.boundingBox();

  // Park the bar just inside the right edge, which is where the defect lives.
  await page.evaluate(
    ({ fn, by }) => {
      const el = new Function(`return (${fn})()`)() as HTMLElement;
      el.scrollLeft += by;
    },
    { fn: SCROLLER.toString(), by: first!.x - (scroller.right - 40) },
  );
  await page.waitForTimeout(400);

  const parked = await bar.boundingBox();
  expect(parked!.x, "the bar is in the edge zone").toBeGreaterThan(scroller.right - 64);

  const settled = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );

  // A click, with the hand moving a pixel as hands do.
  await page.mouse.move(parked!.x + 4, parked!.y + parked!.height / 2);
  await page.mouse.down();
  await page.mouse.move(parked!.x + 5, parked!.y + parked!.height / 2);
  await page.waitForTimeout(120);
  await page.mouse.up();
  await page.waitForTimeout(800);

  const moved = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollLeft,
    SCROLLER.toString(),
  );

  // Two assertions, cause and effect.
  //
  // The CAUSE: a press that has not travelled must not scroll. That is
  // `DRAG_SLOP`, observed directly.
  expect(moved - settled, "a click must not scroll the chart").toBe(0);
  // The EFFECT: and so it must not move the task either. This is the one
  // that would have reached a member — a bar they clicked, silently two days
  // later than it was.
  expect(writes, "a click must not write a new date").toEqual([]);

  // ⚠️ NOT asserted here: that the task panel opened. It is the other half
  // of the reported symptom and it does depend on this fix — the component
  // suppresses the open when the gesture travelled. But opening runs through
  // `openWithStatuses` on the page, which needs more of the app stubbed than
  // this file has any business knowing about, and a test that fails for a
  // missing stub is a test that lies about its subject. The two assertions
  // above pin the cause and the damage; the open follows from the cause.
});


test("a BAR drag does not scroll the chart vertically", async ({ page }) => {
  test.setTimeout(120_000);

  // ⚠️ Found in review, and it is the feature's own failure mode on the other
  // axis. A bar cannot change rows, so vertical travel buys it nothing — and
  // costs it everything. Drag a bar on one of the last visible rows and the
  // pointer sits in the bottom 64px zone for the whole gesture: the chart
  // scrolls at up to 15 rows a second and the bar you are holding leaves the
  // top of the screen. Dragging blind is what this feature set out to end.
  await openTimeline(page);

  const view = await page.evaluate((fn) => {
    const el = new Function(`return (${fn})()`)() as HTMLElement;
    const box = el.getBoundingClientRect();
    return { bottom: box.bottom, right: box.right, scrollTop: el.scrollTop, maxTop: el.scrollHeight - el.clientHeight };
  }, SCROLLER.toString());
  expect(view.maxTop, "the chart is taller than its container").toBeGreaterThan(100);

  const bar = page.locator('[data-bar="t1"]');
  const barBox = await bar.boundingBox();

  await page.mouse.move(barBox!.x + barBox!.width / 2, barBox!.y + barBox!.height / 2);
  await page.mouse.down();
  // Into the bottom-right corner: sideways SHOULD move, downwards must not.
  await page.mouse.move(view.right - 20, view.bottom - 20, { steps: 10 });
  await page.waitForTimeout(900);
  const after = await page.evaluate(
    (fn) => {
      const el = new Function(`return (${fn})()`)() as HTMLElement;
      return { left: el.scrollLeft, top: el.scrollTop };
    },
    SCROLLER.toString(),
  );
  await page.mouse.up();

  expect(after.top, "a bar drag must not scroll vertically").toBe(view.scrollTop);
  // And the horizontal half still works from the same corner, so this test
  // cannot pass by the loop simply being dead.
  expect(after.left, "sideways still moves").toBeGreaterThan(150);
});

test("a DEPENDENCY drag DOES scroll vertically, to reach a task below the fold", async ({ page }) => {
  test.setTimeout(120_000);

  // The other side of the ruling above. Vertical is not removed, it is given
  // to the one gesture whose target is a different row — a blocker and the
  // task it blocks are usually far apart in the list.
  await openTimeline(page);

  const view = await page.evaluate((fn) => {
    const el = new Function(`return (${fn})()`)() as HTMLElement;
    const box = el.getBoundingClientRect();
    return { bottom: box.bottom, scrollTop: el.scrollTop };
  }, SCROLLER.toString());

  const dot = page.locator('[data-link-from="t1"]');
  await dot.scrollIntoViewIfNeeded();
  const dotBox = await dot.boundingBox();
  expect(dotBox, "the dependency handle is reachable").not.toBeNull();

  await page.mouse.move(dotBox!.x + dotBox!.width / 2, dotBox!.y + dotBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(dotBox!.x, view.bottom - 20, { steps: 10 });
  await page.waitForTimeout(900);
  const after = await page.evaluate(
    (fn) => (new Function(`return (${fn})()`)() as HTMLElement).scrollTop,
    SCROLLER.toString(),
  );
  await page.mouse.up();

  expect(after, "the arrow can reach a row below the fold").toBeGreaterThan(50);
});
