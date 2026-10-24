import { expect, test, type Page } from "@playwright/test";

import { VIEW_TITLES } from "../src/app/tasks/lib/viewTitles";

/**
 * My work in the Projects sidebar (WS-64 OP-2, `one_projects_app.md` §4.1 and
 * §9 OP-2).
 *
 * ## The rules
 *
 * 1. With the flag on, the sidebar shows six entries under "My work".
 * 2. Each count equals the count that `/tasks` shows for the same member.
 * 3. `?app=my-today` opens Today, and `/projects` with nothing opens Today.
 *
 * ## How to run it
 *
 * The flag is inlined at build time, so the dev server must start with it:
 *
 *     NEXT_PUBLIC_PROJECTS_MY_WORK=1 npx playwright test e2e/projects-my-work.spec.ts
 *
 * Without the flag the file skips. A vitest test cannot see the page
 * (`src/app/projects/lib/myWork.test.ts` fences the rules).
 */

test.skip(
  !["1", "true", "on", "yes"].includes((process.env.NEXT_PUBLIC_PROJECTS_MY_WORK ?? "").toLowerCase()),
  "start the server with NEXT_PUBLIC_PROJECTS_MY_WORK=1",
);

/** The date the gateway answers, and a due date on or before it. */
const TODAY = "2026-10-24";

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: `Task ${id}`,
    disposition: "NEXT",
    is_mine: true,
    project_id: "root-1",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    assignees: [],
    tags: [],
    ...over,
  };
}

/**
 * The gateway answers `my/inbox` with every open row, whatever its disposition,
 * so the store counts all of them. The test never assumes a number: it compares
 * the settled counts of the two pages.
 *
 * 2 inbox, 3 next (2 of them dated today or before), 1 waiting, 1 someday. */
const ROWS = [
  row("i1", { disposition: "INBOX" }),
  row("i2", { disposition: "INBOX" }),
  row("n1", { due_at: "2026-10-24" }),
  row("n2", { due_at: "2026-10-01" }),
  row("n3"),
  row("w1", { disposition: "WAITING" }),
  row("s1", { disposition: "SOMEDAY" }),
];

const FIXTURES: Record<string, unknown> = {
  "auth/me": { email: "you@example.com", name: "You" },
  "projects/tree": { rows: [], total: 0 },
  "my/today": { today: TODAY, timezone: "UTC", stored: true, valid: true },
  "my/inbox": { rows: ROWS, total: ROWS.length },
};
const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

async function mock(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });
}

const ENTRIES = Object.values(VIEW_TITLES);

/** The number in a sidebar entry, or null when it shows none. */
async function countOf(page: Page, name: string): Promise<number | null> {
  const button = page.getByRole("button", { name: new RegExp(`^${name}`) }).first();
  const text = (await button.innerText()).replace(name, "").trim();
  return /^\d+$/.test(text) ? Number(text) : null;
}

test("the section shows the six entries", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects");
  await expect(page.getByText("My work", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  for (const name of ENTRIES) {
    await expect(page.getByRole("button", { name: new RegExp(`^${name}`) }).first()).toBeVisible();
  }
});

test("/projects with nothing opens Today", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects");
  await expect(page.getByRole("button", { name: /^Today/ }).first()).toHaveAttribute("aria-pressed", "true", {
    timeout: 30_000,
  });
  // Today holds the two next actions dated today or before.
  await expect(page.getByText("Task n1", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Task n2", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Task n3", { exact: true })).toHaveCount(0);
});

test("?app=my-waiting opens its view", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/projects?app=my-waiting");
  await expect(page.getByRole("button", { name: /^Waiting For/ }).first()).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 30_000 },
  );
});

/**
 * Read until three reads in a row agree. The store fills in steps, so a count
 * seen once can be a value on the way to the settled one. The test compares
 * only the settled value, never a number it expects in advance.
 */
async function settled<T>(read: () => Promise<T>): Promise<T> {
  let last = await read();
  let same = 0;
  for (let i = 0; i < 100 && same < 3; i++) {
    await new Promise((r) => setTimeout(r, 200));
    const now = await read();
    same = JSON.stringify(now) === JSON.stringify(last) ? same + 1 : 0;
    last = now;
  }
  expect(same, "the counts never settled").toBeGreaterThanOrEqual(3);
  return last;
}

// WS-64 OP-4: with the flag on (the only way this file runs), `/tasks` is a
// redirect into Projects, so the page this test compared against no longer
// draws. `myWork.test.ts` ("myWorkCounts") fences the same equality on the two
// functions both pages call.
test.fixme("each count equals the count that /tasks shows", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);

  await page.goto("/projects");
  await expect(page.getByRole("button", { name: /^Today/ }).first()).toHaveAttribute("aria-pressed", "true", {
    timeout: 30_000,
  });
  // The store loads when the view opens. Wait for a count to show, then for it to stop moving.
  await expect(page.getByRole("button", { name: /^Inbox\s*\d/ }).first()).toBeVisible({ timeout: 30_000 });
  const mine = await settled(async () => ({
    inbox: await countOf(page, "Inbox"),
    next: await countOf(page, VIEW_TITLES.next),
    waiting: await countOf(page, VIEW_TITLES.waiting),
    someday: await countOf(page, VIEW_TITLES.someday),
  }));

  await page.goto("/tasks");
  const nav = page.getByRole("navigation").filter({ hasText: "Your lists" });
  await expect(nav.getByRole("button", { name: /^Inbox\s*\d/ })).toBeVisible({ timeout: 30_000 });
  const theirs = async (name: string) => {
    const text = (await nav.getByRole("button", { name: new RegExp(`^${name}`) }).first().innerText())
      .replace(name, "")
      .trim();
    return /^\d+$/.test(text) ? Number(text) : null;
  };
  const tasks = await settled(async () => ({
    inbox: await theirs("Inbox"),
    next: await theirs(VIEW_TITLES.next),
    waiting: await theirs(VIEW_TITLES.waiting),
    someday: await theirs(VIEW_TITLES.someday),
  }));
  expect(mine.inbox).not.toBeNull();
  expect(mine).toEqual(tasks);
});

/**
 * WS-64 OP-3 — the Personal section. The tree answer carries the caller's own
 * tree in `personal`, and a team node kept under the caller's root in `rows`.
 * Playwright runs the last registered route first, so this overrides `mock`.
 */
async function mockTree(page: Page) {
  const personalArea = {
    id: "p-area",
    name: "Home admin",
    kind: "folder",
    parent_project_id: "p-root",
    personal: true,
    can_delete: true,
    can_write_grants: false,
    tasks: 2,
  };
  const personal = [
    {
      id: "p-root",
      name: "Personal space",
      personal: true,
      can_delete: false,
      can_write_grants: false,
      tasks: 2,
      children: [personalArea],
    },
  ];
  const rows = [
    { id: "t-space", name: "Team space", tasks: 1 },
    // The server (`tree.py` `_nest`) makes a team node whose parent is the
    // personal root a ROOT of `rows`.
    { id: "t-kept", name: "Kept team project", parent_project_id: "p-root" },
  ];
  await page.route("**/api/**/projects/tree", (route) =>
    route.fulfill({ json: { rows, total: 1, personal } }),
  );
}

test("the Personal section sits between My work and Spaces, with a lock", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await mockTree(page);
  await page.goto("/projects");
  const section = page.getByTestId("personal-section");
  await expect(section).toBeVisible({ timeout: 30_000 });
  await expect(section.getByText("Personal", { exact: true }).first()).toBeVisible();
  await expect(section.getByText("Personal space")).toBeVisible();
  // The team node kept under the caller's root is a root of Spaces: visible
  // outside the personal section, and absent inside it.
  await expect(section.getByText("Kept team project")).toHaveCount(0);
  await expect(page.getByText("Kept team project", { exact: true })).toBeVisible();
  await expect(page.getByText("Team space", { exact: true }).first()).toBeVisible();
  // The personal root's menu has no Move and no Archive.
  await section.getByText("Personal space").click({ button: "right" });
  const names = await page.getByRole("menuitem").allTextContents();
  expect(names.length).toBeGreaterThan(0);
  expect(names.join("|")).not.toMatch(/move|archive|share|access/i);
});
