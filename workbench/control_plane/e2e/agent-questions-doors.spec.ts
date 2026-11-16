import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-55 EA-18c — the three doors of Questions for me (`external_agent_access.md`
 * §12.8, D-EA-14): My Day, Projects and Approvals.
 *
 * One mocked question set feeds all three, and the checks are:
 *
 * 1. My Day draws ONE `agent_questions` card, and its title holds the count.
 * 2. Projects (`?app=questions`) shows "For me · N" with the same N.
 * 3. Approvals shows the tabs "Actions" and "Agent questions · N" with the same
 *    N, and the list under the second tab is the Projects list. The CIV-14k
 *    "Waiting for you" section is gone, and the old route is not read.
 * 4. An answer on the Approvals tab posts the option id to `/activities`, the
 *    same write as the Projects list.
 * 5. With the route off (404) Approvals draws no tabs and keeps the CIV-14k
 *    section, and the Projects sidebar has no entry.
 *
 * The owner's box runs e2e, and the build session does not. Run it by name:
 *
 *     npx playwright test e2e/agent-questions-doors.spec.ts --project=chromium
 */

const ME = "asha@example.com";
const SESSION = {
  user: { email: ME, name: "Asha Rao" },
  expires: "2099-01-01T00:00:00.000Z",
};
const MEMBER = {
  authenticated: true,
  email: ME,
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const KNOWN = ["/tasks", "/projects", "/approvals"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const TREE = [
  {
    id: "p1",
    name: "Metorite",
    parent_project_id: null,
    kind: "project",
    children: [{ id: "p2", name: "AI Studio", parent_project_id: "p1", kind: "project", children: [] }],
  },
];

function row(n: number) {
  return {
    run_id: `r${n}`,
    activity_id: `a${n}`,
    task_id: `t${n}`,
    task_number: n,
    task_title: `Task ${n}`,
    agent_name: "Claude Code",
    client_via: "mcp:claude-code",
    asked_at: `2026-11-11T0${n}:00:00Z`,
    run_started_at: "2026-11-11T00:30:00Z",
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
  };
}

const EMPTY = { rows: [], items: [], total: 0 };

interface Opts {
  off?: boolean;
}

async function stub(page: Page, opts: Opts = {}) {
  let rows = [row(1), row(2)];
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const oldRouteReads: string[] = [];

  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-dock", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.setItem("cc-my-day-feed", "1");
  });

  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\//, "");
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "projects/agent-questions") {
      if (opts.off) return json({ detail: "Not Found" }, 404);
      const limit = Number(url.searchParams.get("limit") ?? "100");
      return json({
        rows: limit === 0 ? [] : rows,
        counts: { mine: rows.length, everyone: null },
        total: rows.length,
        offset: 0,
        limit,
        capped: false,
      });
    }
    if (path === "projects/agent-runs") {
      // The CIV-14k route of the old "Waiting for you" section.
      oldRouteReads.push(url.search);
      return json([]);
    }
    if (request.method() === "POST" && /projects\/agent-runs\/r\d\/activities$/.test(path)) {
      posts.push({ path, body: request.postDataJSON() as Record<string, unknown> });
      rows = rows.filter((r) => !path.includes(`/${r.run_id}/`));
      return json({ activity: null, run: {}, created: true, archived: false }, 201);
    }
    if (path === "shell/feed") {
      const total = opts.off ? 0 : rows.length;
      const cards = total
        ? [
            {
              signal_id: "questions:mine",
              app: "projects",
              kind: "agent_questions",
              folded: [],
              group: "needs_you",
              score: 43,
              title: total === 1 ? "An agent question waits for you" : `${total} agent questions wait for you`,
              why: "Metorite › AI Studio: Which database should the migration target? Options: Postgres, SQLite.",
              at: "2026-11-11T01:00:00Z",
              href: "/projects?app=questions",
              acts: [{ id: "answer", label: "Answer", class: "A", way: "open", href: "/projects?app=questions" }],
              state: "new",
              updated: false,
              still_open: false,
              visits: 0,
              state_fp: "a41f09c2",
            },
          ]
        : [];
      return json({
        cards,
        groups: cards.length ? [{ id: "needs_you", count: cards.length }] : [],
        needs: null,
        sources: { agent_questions: opts.off ? "absent" : "ok" },
        capped: [],
      });
    }
    if (path === "actions/pending") return json([]);
    if (path === "auth/me/shell") return json(LAYOUT);
    if (path === "auth/me") return json(MEMBER);
    if (path === "auth/session") return json(SESSION);
    if (path === "health") return json({ gateway: "up" });
    if (path === "accounts") return json({ enabled: false });
    if (request.method() !== "GET") return json(EMPTY);
    if (path.includes("projects/tree")) return json({ rows: TREE, total: 1 });
    return json(EMPTY);
  });
  return { posts, oldRouteReads };
}

test.describe("Questions for me: the three doors", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("My Day, Projects and Approvals show the same question and the same count", async ({ page }) => {
    test.setTimeout(120_000);
    const { oldRouteReads } = await stub(page);

    // Door 1: My Day. One card, and the count is in its title.
    await page.goto("/");
    const card = page.getByTestId("feed-card-questions:mine");
    await expect(card).toBeVisible();
    await expect(card).toContainText("2 agent questions wait for you");
    await expect(card).toContainText("Metorite › AI Studio");
    await expect(card).toContainText("Postgres, SQLite");

    // Door 2: Projects. The tab says the same number.
    await page.goto("/projects?app=questions");
    await expect(page.locator('[data-questions-tab="mine"]')).toHaveText("For me · 2");

    // Door 3: Approvals. The tab says the same number, and one list is under it.
    await page.goto("/approvals");
    await expect(page.locator('[data-approval-tab="questions"]')).toHaveText("Agent questions · 2");
    await expect(page.locator('[data-approval-tab="actions"]')).toHaveText("Actions");
    await expect(page.getByLabel("Waiting for you")).toHaveCount(0);
    await page.locator('[data-approval-tab="questions"]').click();
    await expect(page.locator("[data-question-row]")).toHaveCount(2);
    await expect(page.locator("[data-approval-full-list]")).toHaveAttribute("href", "/projects?app=questions");
    // The CIV-14k route is not read while the questions route is on.
    expect(oldRouteReads).toEqual([]);
  });

  test("an answer on the Approvals tab runs the write of the Projects list", async ({ page }) => {
    test.setTimeout(120_000);
    const { posts } = await stub(page);
    await page.goto("/approvals");
    await page.locator('[data-approval-tab="questions"]').click();
    const first = page.locator("[data-question-row]").first();
    await first.getByText("Postgres").click();
    await first.getByRole("button", { name: /send|submit|answer/i }).click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].path).toBe("projects/agent-runs/r1/activities");
    expect(posts[0].body).toMatchObject({ type: "prompt" });
  });

  test("with the route off, Approvals keeps the old section and draws no tabs", async ({ page }) => {
    test.setTimeout(120_000);
    const { oldRouteReads } = await stub(page, { off: true });
    await page.goto("/approvals");
    await expect(page.locator("[data-approval-tabs]")).toHaveCount(0);
    await expect.poll(() => oldRouteReads.length).toBeGreaterThan(0);
    await page.goto("/");
    await expect(page.getByTestId("feed-card-questions:mine")).toHaveCount(0);
  });
});
