import { expect, test, type Page } from "@playwright/test";

/**
 * WS-55 EA-18b — Questions for me in Projects (`external_agent_access.md`
 * §12.8, D-EA-14).
 *
 * `GET /projects/agent-questions` is mocked through `page.route`, in the shape
 * of `routes/projects/agent_questions.py`. Checks:
 *
 * 1. With the route on, the sidebar shows "Questions" with the "mine" count,
 *    and `?app=questions` opens the pane. Rows group under their project path,
 *    and each name links to its project.
 * 2. "Everyone" shows only when the server sent its count.
 * 3. An option answer posts the option id to `/activities`, and the row reads
 *    "Answered by <name>" and then leaves the list.
 * 4. A typed archive request posts to `archive-approval`, never `/activities`.
 * 5. With the route off (404) the sidebar has no entry.
 * 6. The empty states, and the capped note.
 *
 * Run it by name (the owner's box runs e2e, not the build session):
 *
 *     npx playwright test e2e/agent-questions.spec.ts --project=chromium
 */

const ME = "you@example.com";

const TREE = [
  {
    id: "p1",
    name: "Metorite",
    parent_project_id: null,
    kind: "project",
    children: [
      { id: "p2", name: "AI Studio", parent_project_id: "p1", kind: "project", children: [] },
    ],
  },
];

function row(over: Record<string, unknown> = {}) {
  return {
    run_id: "r1",
    activity_id: "a1",
    task_id: "t1",
    task_number: 7,
    task_title: "Pick a database",
    agent_name: "Claude Code",
    client_via: "mcp:claude-code",
    asked_at: "2026-11-11T09:00:00Z",
    run_started_at: "2026-11-11T08:30:00Z",
    project_path: [
      { id: "p1", name: "Metorite" },
      { id: "p2", name: "AI Studio" },
    ],
    question: {
      kind: "question",
      body: "Which database should the migration target?",
      options: [
        { id: "pg", label: "Postgres", recommended: true },
        { id: "sqlite", label: "SQLite" },
      ],
      allow_multiple: false,
    },
    can_answer: true,
    why: "created",
    ...over,
  };
}

const ARCHIVE = row({
  run_id: "r2",
  activity_id: "a2",
  task_id: "t2",
  task_number: 8,
  task_title: "Retire the old runner",
  why: "my_agent",
  question: {
    kind: "archive_request",
    body: "The agent asks to archive this task.",
    expires_at: "2099-01-01T00:00:00+00:00",
    options: [
      { id: "approve", label: "Approve" },
      { id: "decline", label: "Decline" },
    ],
    allow_multiple: false,
  },
});

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

async function stub(
  page: Page,
  opts: {
    off?: boolean;
    everyone?: number | null;
    rows?: Record<string, unknown>[];
    capped?: boolean;
  } = {},
) {
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const queries: string[] = [];
  let rows = opts.rows ?? [row(), ARCHIVE];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\//, "");

    if (path === "projects/agent-questions") {
      queries.push(url.search);
      if (opts.off) return route.fulfill({ status: 404, json: { detail: "Not Found" } });
      const limit = Number(url.searchParams.get("limit") ?? "100");
      return route.fulfill({
        json: {
          rows: limit === 0 ? [] : rows,
          counts: { mine: rows.length, everyone: opts.everyone ?? null },
          total: rows.length,
          offset: 0,
          limit,
          capped: Boolean(opts.capped),
        },
      });
    }
    if (request.method() === "POST" && /projects\/agent-runs\/r\d\/(activities|archive-approval)$/.test(path)) {
      posts.push({ path, body: request.postDataJSON() as Record<string, unknown> });
      rows = rows.filter((r) => !path.includes(String(r.run_id)));
      return route.fulfill({ status: 201, json: { activity: null, run: {}, created: true, archived: false } });
    }
    if (request.method() !== "GET") return route.fulfill({ json: EMPTY });
    if (path.includes("projects/tree")) return route.fulfill({ json: { rows: TREE, total: 1 } });
    if (path.includes("auth/me")) return route.fulfill({ json: { email: ME, name: "You" } });
    return route.fulfill({ json: EMPTY });
  });

  await page.goto("/projects?app=questions");
  await page.waitForLoadState("domcontentloaded");
  return { posts, queries };
}

test("the sidebar shows Questions with the count, and the pane groups by project", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page);

  const entry = page.getByRole("button", { name: /^Questions/ }).first();
  await expect(entry).toBeVisible({ timeout: 30_000 });
  await expect(entry).toContainText("2");

  const pane = page.locator("[data-questions]");
  await expect(pane).toBeVisible();
  const group = pane.locator("[data-questions-group]").first();
  await expect(group).toContainText("Metorite");
  await expect(group).toContainText("AI Studio");
  await expect(group).toContainText("2 questions");
  // Each name links to its project.
  await expect(group.locator("[data-questions-project]").first()).toHaveAttribute(
    "href",
    "/projects?project=p1",
  );
  await expect(pane.getByText("You created this task")).toBeVisible();
  await expect(pane.getByText("Your agent asked")).toBeVisible();
  await expect(pane.getByText("Claude Code asked", { exact: false }).first()).toBeVisible();
});

test("the Everyone tab shows only when the server sent its count", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, { everyone: null });
  await expect(page.locator("[data-questions-tab=mine]")).toContainText("For me · 2", { timeout: 30_000 });
  await expect(page.locator("[data-questions-tab=everyone]")).toHaveCount(0);
});

test("a reviewer sees Everyone · M and asks the route for that scope", async ({ page }) => {
  test.setTimeout(120_000);
  const { queries } = await stub(page, { everyone: 8 });
  const tab = page.locator("[data-questions-tab=everyone]");
  await expect(tab).toContainText("Everyone · 8", { timeout: 30_000 });
  await tab.click();
  await expect.poll(() => queries.some((q) => q.includes("scope=everyone"))).toBe(true);
});

test("an option answer posts the option id, reads Answered by, and leaves the list", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts } = await stub(page);
  const pane = page.locator("[data-questions]");
  const card = pane.locator('[data-question-row="question"]');
  await expect(card).toBeVisible({ timeout: 30_000 });

  await card.locator("[data-elicitation-option]").filter({ hasText: "Postgres" }).click();
  await card.locator("[data-elicitation-submit]").click();

  await expect(card.locator("[data-agent-answered]")).toHaveText(/Answered by you: Postgres/i, {
    timeout: 10_000,
  });
  expect(posts).toHaveLength(1);
  expect(posts[0].path).toBe("projects/agent-runs/r1/activities");
  expect(posts[0].body).toMatchObject({ type: "prompt", selected: ["pg"] });
  // The list reads again and the row leaves.
  await expect(pane.locator('[data-question-row="question"]')).toHaveCount(0, { timeout: 10_000 });
});

test("a typed archive request goes to archive-approval and never to activities", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts } = await stub(page);
  const card = page.locator('[data-question-row="archive_request"]');
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.locator("textarea")).toHaveCount(0);

  await card.locator("[data-elicitation-option]").filter({ hasText: "Approve" }).click();
  await card.locator("[data-elicitation-submit]").click();

  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].path).toBe("projects/agent-runs/r2/archive-approval");
  expect(posts[0].body).toMatchObject({ decision: "approve" });
  expect(posts.some((p) => p.path.endsWith("/activities"))).toBe(false);
});

test("an expired archive request draws Decline only", async ({ page }) => {
  test.setTimeout(120_000);
  const expired = row({
    ...ARCHIVE,
    question: { ...ARCHIVE.question, expires_at: "2020-01-01T00:00:00+00:00" },
  });
  const { posts } = await stub(page, { rows: [expired] });
  const card = page.locator('[data-question-row="archive_request"]');
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.locator('[data-archive-request="expired"]')).toBeVisible();
  await expect(card.locator("[data-archive-decline]")).toBeVisible();
  await expect(card.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(card.locator("[data-elicitation-option]")).toHaveCount(0);

  await card.locator("[data-archive-decline]").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].path).toBe("projects/agent-runs/r2/archive-approval");
  expect(posts[0].body).toMatchObject({ decision: "decline" });
});

test("with the route off, the sidebar has no Questions entry", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, { off: true });
  await expect(page.getByRole("button", { name: /^Reports/ }).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^Questions/ })).toHaveCount(0);
  await expect(page.getByText("Questions are not on for your organization.")).toBeVisible();
});

test("the empty states and the capped note", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, { rows: [], everyone: 0 });
  await expect(page.getByText("No question waits on you.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("When an agent asks, it shows here.")).toBeVisible();
  await page.locator("[data-questions-tab=everyone]").click();
  await expect(page.getByText("No agent waits on anyone.")).toBeVisible();
});

test("a capped page says it shows the oldest rows", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, { capped: true });
  await expect(page.locator("[data-questions-capped]")).toHaveText(/Showing the oldest 2/, {
    timeout: 30_000,
  });
});
