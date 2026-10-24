import { expect, test, type Page } from "@playwright/test";

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

/** 2 inbox, 3 next (2 of them dated today or before), 1 waiting, 1 someday. */
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

const ENTRIES = ["Inbox", "Today", "Next actions", "Waiting for", "Someday / Maybe", "Done"];

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
  await expect(page.getByRole("button", { name: /^Waiting for/ }).first()).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 30_000 },
  );
});

test("each count equals the count that /tasks shows", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);

  await page.goto("/projects");
  await expect(page.getByRole("button", { name: /^Today/ }).first()).toHaveAttribute("aria-pressed", "true", {
    timeout: 30_000,
  });
  // The store loads when the view opens, so wait for the Inbox count.
  await expect(page.getByRole("button", { name: /^Inbox\s*2/ }).first()).toBeVisible({ timeout: 30_000 });
  const mine = {
    inbox: await countOf(page, "Inbox"),
    next: await countOf(page, "Next actions"),
    waiting: await countOf(page, "Waiting for"),
    someday: await countOf(page, "Someday / Maybe"),
  };

  await page.goto("/tasks");
  const nav = page.getByRole("navigation").filter({ hasText: "Your lists" });
  await expect(nav.getByRole("button", { name: /^Inbox\s*2/ })).toBeVisible({ timeout: 30_000 });
  const theirs = async (name: string) => {
    const text = (await nav.getByRole("button", { name: new RegExp(`^${name}`) }).first().innerText())
      .replace(name, "")
      .trim();
    return /^\d+$/.test(text) ? Number(text) : null;
  };
  expect(mine.inbox).toBe(await theirs("Inbox"));
  expect(mine.next).toBe(await theirs("My Next Actions"));
  expect(mine.waiting).toBe(await theirs("Waiting For"));
  expect(mine.someday).toBe(await theirs("Someday / Maybe"));
});
