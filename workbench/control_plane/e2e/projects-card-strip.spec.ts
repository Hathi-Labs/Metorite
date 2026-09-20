import { expect, test } from "@playwright/test";

/**
 * The board card's hover strip, from the keyboard (owner direction 2026-09-20).
 *
 * ## Why this file exists, and why a unit test cannot replace it
 *
 * `vitest.config.ts` is `environment: "node"`. The defect this pins is a DOM
 * event-propagation fact and nothing in the unit suite can see it:
 *
 * > `TaskCardShell` turns Enter and Space into "open the task", and it does so
 * > for a keydown raised by ANY descendant, with `preventDefault()`. That
 * > cancels the click the browser would have synthesised for a focused
 * > `<button>`. So pressing Enter on "Mark done" opened the task panel, the
 * > action never ran, and the board's cursor stepper then opened a second
 * > task as well.
 *
 * It shipped green, it was invisible in review, and the file header of
 * `TaskCardActions.tsx` claimed the opposite ("`opacity-0` keeps the buttons
 * focusable"). Focusable is not reachable when something upstream eats the
 * key. Found in adversarial review, 2026-09-20.
 *
 * ⚠️ **H-27: nothing in CI runs `e2e/`, so this is not yet a gate.** It is a
 * real, runnable check —
 *
 *     npx playwright test e2e/projects-card-strip.spec.ts --project=chromium
 *
 * — and it goes red the moment `SWALLOW` loses its `onKeyDown`. Naming that
 * honestly rather than calling it a fence it is not (R7).
 */

const STATUSES = [
  { id: "s1", project_id: "p2", name: "Backlog", color: "gray", position: 10, category: "backlog", is_default: false },
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: false },
  { id: "s4", project_id: "p2", name: "Done", color: "green", position: 40, category: "done", is_default: false },
];

const TASK3 = {
  id: "t4",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 4,
  status_id: "s2",
  title: "Third card",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

const TASK2 = {
  id: "t3",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 3,
  status_id: "s2",
  title: "Second card",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

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
  "projects/tasks": { rows: [TASK, TASK2, TASK3], total: 3 },
};

test("Enter on a strip button runs the action, and does not open the task", async ({
  page,
}) => {
  test.setTimeout(120_000);

  /** Every write the page sent — "did it act" is answered by evidence. */
  const writes: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\//, "");

    if (request.method() === "PATCH") {
      writes.push(`${path} ${request.postData() ?? ""}`);
      return route.fulfill({ json: { ...TASK, status_id: "s4" } });
    }
    // Longest match wins, so `nodes/p2/statuses` beats a shorter key.
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
  await page.waitForTimeout(2500);

  const row = page
    .locator("li")
    .filter({ hasText: "Notification engine for projects" })
    .last();
  const tick = row.getByRole("button", { name: "Mark done", exact: true });
  await expect(tick).toHaveCount(1);

  // Focus it the way a keyboard user arrives at it — no hover, no click. The
  // strip is `opacity-0` until focus, and `focus-within` is what paints it.
  await tick.focus();
  await expect(tick).toBeFocused();

  await page.keyboard.press("Enter");
  await page.waitForTimeout(1200);

  expect(
    writes.join("|"),
    "Enter on Mark done sent no write. The shell's Enter handler swallowed " +
      "the key and cancelled the button's click — see this file's header.",
  ).toContain('"status_id":"s4"');

  // The other half of the same defect: the key must not ALSO reach the card.
  // The panel mounts a Comment control and a task heading; neither should be
  // here, because nothing asked to open anything.
  await expect(
    page.getByRole("button", { name: "Comment", exact: true }),
    "Enter on a strip button opened the task panel as well as (or instead " +
      "of) running the action.",
  ).toHaveCount(0);
});

test("a slow rename on one card does not close or disable another's", async ({
  page,
}) => {
  // The defect, measured in adversarial review 2026-09-20: `renameBusy` was
  // one board-wide boolean and `setRenaming(null)` ran unconditionally after
  // the await. Rename A, then click Rename on B before A's PATCH returns, and
  // B opened DISABLED and unfocused — then closed by itself when A resolved.
  test.setTimeout(120_000);

  // Initialised to a no-op rather than null: TypeScript cannot see that the
  // Promise executor runs synchronously, so a `| null` here narrows to
  // `never` at the call below.
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api\//, "");
    if (request.method() === "PATCH") {
      // Hold the FIRST rename open until the test lets it go.
      await held;
      return route.fulfill({ json: { ...TASK, title: "Renamed" } });
    }
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });

  await page.goto("/projects");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(4000);
  await page.getByText("Bootloader", { exact: true }).first().click();
  await page.waitForTimeout(2500);

  const rowOf = (title: string) =>
    page.locator("li").filter({ hasText: title }).last();

  // ⚠️ Hover first. The strip is `pointer-events-none` until the card is
  // hovered, so a bare `.click()` waits for actionability and times out —
  // which is the guard working, not a flake.
  const openRename = async (title: string) => {
    await page.getByText(title, { exact: true }).first().hover();
    await page.waitForTimeout(250);
    await rowOf(title).getByRole("button", { name: "Rename", exact: true }).click();
  };

  // A: open the field, change the title, and commit — the PATCH now hangs.
  await openRename("Notification engine for projects");
  const fieldA = page.getByLabel("Rename Notification engine for projects");
  await expect(fieldA).toBeVisible();
  await fieldA.fill("Renamed");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);

  // B: open its field while A is still in flight.
  await openRename("Second card");
  const fieldB = page.getByLabel("Rename Second card");
  await expect(fieldB).toBeVisible();
  await expect(
    fieldB,
    "B's field opened read-only because the busy flag was board-wide.",
  ).not.toHaveAttribute("readonly", /.*/);
  await expect(fieldB, "B's field did not take focus.").toBeFocused();

  // Let A finish. B must survive it.
  release();
  await page.waitForTimeout(1500);
  await expect(
    page.getByLabel("Rename Second card"),
    "A's response closed B's field — `setRenaming(null)` was unconditional.",
  ).toBeVisible();
});

/**
 * ── The touch-capable display (owner report, 2026-09-20) ────────────────
 *
 * The regression this pins is NOT "does hover work". It is that Tailwind v4
 * compiles `hover:` and `group-hover:` inside `@media (hover: hover)`, and
 * Chromium reports `hover: none` for any touch-capable display — a Windows
 * laptop with a touchscreen driven by a mouse included.
 *
 * Both possible mistakes ship green on a normal desktop, and both were made:
 *
 *  * `group-hover:` alone → the strip is invisible FOR EVER on such a
 *    display, because the media query never matches.
 *  * `group-hover:` plus an `@media (hover: none)` pin → the strip and the
 *    card's checkbox are permanently ON for exactly those members. That is
 *    what the owner saw and reported.
 *
 * So this runs the board in a `hasTouch` context and asserts both ends:
 * nothing at rest, everything on hover.
 */
test.describe("on a touch-capable display", () => {
  test.use({ hasTouch: true });

  test("the strip hides at rest and reveals on hover", async ({ page }) => {
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
    await page.getByText("Bootloader", { exact: true }).first().click();
    await page.waitForTimeout(2500);

    // The premise. If this is false the test is not exercising the bug.
    expect(
      await page.evaluate(() => matchMedia("(hover: none)").matches),
      "this context does not report `hover: none`, so it cannot see the bug",
    ).toBe(true);

    const card = page.getByText("Notification engine for projects").first();
    const row = page
      .locator("li")
      .filter({ hasText: "Notification engine for projects" })
      .last();
    const strip = row.locator("[aria-label='More actions']").locator("..");

    // ── at rest ──────────────────────────────────────────────────────────
    await page.mouse.move(5, 5);
    await page.waitForTimeout(500);
    expect(
      await strip.evaluate((el) => getComputedStyle(el).opacity),
      "the strip is visible with nothing hovered — an `@media (hover: none)` " +
        "pin is back, or something else is forcing it on.",
    ).toBe("0");
    await expect(
      row.locator('input[type="checkbox"]'),
      "a board card grew a checkbox of its own. There is no gutter any more " +
        "(owner, 2026-09-20) — the tick lives in the card's action pill so " +
        "the card is full width at all times.",
    ).toHaveCount(0);

    // ── on hover ─────────────────────────────────────────────────────────
    await card.hover();
    await page.waitForTimeout(500);
    expect(
      await strip.evaluate((el) => getComputedStyle(el).opacity),
      "the strip did not reveal on hover. Tailwind's `hover:` is wrapped in " +
        "`@media (hover: hover)`, which is FALSE here — the reveal must use " +
        "the arbitrary `[[data-card]:hover_&]` variant instead.",
    ).toBe("1");

    // The tick box the owner asked for, at the end of the pill.
    await expect(
      row.getByRole("button", { name: "Select", exact: true }),
      "the strip has no Select control, so with the card's checkbox gone " +
        "there is no way to start a selection without a right-click.",
    ).toHaveCount(1);
  });

  test("a selected card keeps its tick lit, and its neighbours stay quiet", async ({
    page,
  }) => {
    // Owner direction, 2026-09-20: no gutter, ever. Selection shows in the
    // shell's border and in a tick that PERSISTS on the card's own pill.
    // Two ways this ships wrong and neither is visible in a unit test:
    // the tick fades with the pointer (nothing then says the card is
    // selected), or every card's pill pins on (the always-on complaint
    // again, wearing a different hat).
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
    await page.getByText("Bootloader", { exact: true }).first().click();
    await page.waitForTimeout(2500);

    const rowOf = (title: string) =>
      page.locator("li").filter({ hasText: title }).last();
    const tickPill = (title: string) =>
      rowOf(title).getByRole("button", { name: /^(Select|Remove from selection)$/ }).locator("..");

    const first = "Notification engine for projects";
    const second = "Second card";

    // No card anywhere carries a checkbox input. The gutter is gone.
    await expect(
      page.locator('input[type="checkbox"]'),
      "the board drew a checkbox outside the pill",
    ).toHaveCount(0);

    await page.getByText(first).first().hover();
    await page.waitForTimeout(400);
    await rowOf(first).getByRole("button", { name: "Select", exact: true }).click();
    await page.waitForTimeout(800);

    // Take the pointer right away, which is the state that matters.
    await page.mouse.move(5, 5);
    await page.waitForTimeout(600);

    expect(
      await tickPill(first).evaluate((el) => getComputedStyle(el).opacity),
      "the selected card's tick faded with the pointer, so nothing but the " +
        "border says it is selected.",
    ).toBe("1");

    expect(
      await tickPill(second).evaluate((el) => getComputedStyle(el).opacity),
      "an UNselected card's tick is showing with nothing hovered.",
    ).toBe("0");

    // The action pill beside it must NOT pin on with the tick.
    const actionPill = rowOf(first).locator("[aria-label='More actions']").locator("..");
    expect(
      await actionPill.evaluate((el) => getComputedStyle(el).opacity),
      "the whole action pill pinned on for a selected card. Only the tick " +
        "should persist.",
    ).toBe("0");

    // And the card says so to a screen reader, which no colour can.
    await expect(
      page.getByRole("button", { name: `${first}, selected` }),
      "the selected card does not announce its state",
    ).toHaveCount(1);
  });

  test("shift-clicking the tick still extends a range", async ({ page }) => {
    // The gutter checkbox used to carry this gesture. It is gone, so the
    // pill's tick is the only checkbox left — if it swallowed the modifier,
    // range selection would have disappeared with the gutter and nothing
    // else here would have noticed.
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
    await page.getByText("Bootloader", { exact: true }).first().click();
    await page.waitForTimeout(2500);

    const pick = async (title: string, shift = false) => {
      await page.getByText(title).first().hover();
      await page.waitForTimeout(400);
      await page
        .locator("li")
        .filter({ hasText: title })
        .last()
        .getByRole("button", { name: /^(Select|Remove from selection)$/ })
        .click(shift ? { modifiers: ["Shift"] } : undefined);
      await page.waitForTimeout(700);
    };

    // ⚠️ THREE cards, and the shift-click skips the middle one. With two
    // cards this test could not fail: dropping the modifier still toggles
    // the second card ON, so "2 selected" appeared either way. It passed
    // against a deliberately broken build, which is how it was caught.
    // Selecting 1 then shift-clicking 3 must take the RANGE — all three.
    await pick("Notification engine for projects");
    await expect(page.getByText("1 selected")).toBeVisible();

    await pick("Third card", true);
    await expect(
      page.getByText("3 selected"),
      "shift-clicking the tick added one card instead of the range — the " +
        "modifier is being dropped between the button and `onToggle`.",
    ).toBeVisible();
  });
});
