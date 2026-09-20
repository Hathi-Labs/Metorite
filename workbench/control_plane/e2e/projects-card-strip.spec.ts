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
  { id: "s1", project_id: "p2", name: "Backlog", color: "gray", position: 10, category: "open", is_default: false },
  { id: "s2", project_id: "p2", name: "To do", color: "blue", position: 20, category: "open", is_default: false },
  { id: "s4", project_id: "p2", name: "Done", color: "green", position: 40, category: "done", is_default: false },
];

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
  "projects/tasks": { rows: [TASK, TASK2], total: 2 },
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
