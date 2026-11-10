import { expect, test, type Page } from "@playwright/test";

/**
 * A bare `#171` in an assistant answer opens the task card (owner report,
 * 2026-10-13, with a screenshot).
 *
 * The owner's turn: "#172 «one surface»" drew as a task pill, and "#171" and
 * "#87" in the same paragraph stayed text. Here the Projects assistant
 * answers with plain "#171", "#87" and "#404". The lookup
 * (`GET /api/projects/search/refs`) knows #171 and #87, and not #404. So
 * #171 and #87 draw as task pills, #404 stays text, and a click on #171
 * opens its task card in Projects.
 *
 * The vitest twins are `src/lib/taskRefs.test.ts`,
 * `src/lib/taskRefResolver.test.ts` and `src/components/chatTaskRef.test.ts`.
 *
 * No gateway runs under this suite. Every request the page makes either has
 * a stub here, or fails at once.
 *
 *     npx playwright test e2e/chat-task-refs.spec.ts --project=chromium
 */

const T171 = "00000000-0000-4000-8000-000000000171";
const T87 = "00000000-0000-4000-8000-000000000087";

const ANSWER =
  "Two follow-ups are open: #171 and #87. The old ticket #404 is gone, and " +
  "`#12` in code stays code.";

const TASK = {
  id: T171,
  project_id: "p2",
  root_project_id: "p1",
  task_number: 171,
  status_id: "s2",
  title: "Composer always steers",
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

const TREE = [
  { id: "p1", name: "Hathi Labs Projects", parent_project_id: null, kind: "project", children: [
    { id: "p2", name: "AI Chat Interface", parent_project_id: "p1", kind: "project", children: [] },
  ] },
];

const STATUSES = [
  { id: "s2", project_id: "p2", name: "In progress", color: "blue", position: 20, category: "in_progress", is_default: true },
];

async function install(page: Page) {
  await page.addInitScript(
    ({ answer, t171, t87, task, tree, statuses }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      w.__refCalls = 0;
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      const empty = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/api/projects/search/refs")) {
          w.__refCalls = (w.__refCalls as number) + 1;
          return json({
            rows: [
              { id: t171, task_number: 171, title: "Composer always steers", status_name: "In progress", category: "in_progress", archived: false },
              { id: t87, task_number: 87, title: "Card answers after restart", status_name: "To do", category: "todo", archived: false },
            ],
          });
        }
        if (url.includes(`/api/projects/tasks/${t171}`) && !url.includes(`${t171}/`)) return json(task);
        if (url.includes("/api/projects/projects/tree")) return json({ rows: tree, total: 1 });
        if (url.includes("/statuses")) return json({ rows: statuses, total: statuses.length });
        if (url.includes("/api/projects/")) return json(empty);
        if (url.includes("/api/chat/active-sessions")) return json([]);
        if (url.includes("/api/auth/me")) {
          return json({
            email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat", "projects"],
            features_denied: [], agents: ["orchestrator", "projects-assistant"], permissions: ["*"],
            capabilities: [], denied: [], is_admin: true,
          });
        }
        if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
        if (url.includes("/api/chat/sessions")) {
          const method = (init?.method ?? "GET").toUpperCase();
          if (url.includes("/room")) return json({ detail: "not a room" }, 404);
          return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
        }
        if (url.includes("/api/agent/chat")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          if (body.reconnect) return json({}, 404);
          const stream = new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              c.enqueue(frame({ type: "delta", content: answer, messageId: "m-1" }));
              c.enqueue(frame({ type: "done" }));
              c.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { answer: ANSWER, t171: T171, t87: T87, task: TASK, tree: TREE, statuses: STATUSES },
  );
  await page.route("**/api/agent/list", (r) =>
    r.fulfill({
      json: [{
        name: "projects-assistant", display_name: "Projects assistant",
        description: "Projects, tasks and the board", agent_runtime: "maf", status: "live", tags: [],
      }],
    }),
  );
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

test.describe("a bare #N in an assistant answer", () => {
  test.setTimeout(120_000);

  test("draws a task pill, and a click opens the task card", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await install(page);
    await page.goto("/chat");
    const picker = page.getByText("New session", { exact: true });
    const box = page.getByRole("textbox", { name: /^Message / });
    await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
    if (await picker.isVisible()) {
      await page.getByRole("button", { name: /Projects, tasks and the board/i }).click();
    }
    await expect(box).toBeEnabled({ timeout: 30_000 });
    await box.fill("What is still open?");
    await page.getByRole("button", { name: "Send", exact: true }).click();

    const pill171 = page.getByRole("link", { name: /Open task #171 Composer always steers/ });
    const pill87 = page.getByRole("link", { name: /Open task #87 Card answers after restart/ });
    await expect(pill171).toBeVisible({ timeout: 20_000 });
    await expect(pill87).toBeVisible();
    await expect(pill171).toHaveAttribute("href", `/projects?task=${T171}`);
    // #404 is no visible task: plain text, no pill. `#12` in code stays code.
    await expect(page.locator("p", { hasText: "The old ticket #404 is gone" })).toBeVisible();
    await expect(page.getByRole("link", { name: /#404/ })).toHaveCount(0);
    await expect(page.locator("code", { hasText: "#12" })).toBeVisible();
    // One answer, one lookup.
    expect(await page.evaluate(() => (window as unknown as { __refCalls: number }).__refCalls)).toBe(1);

    await page.screenshot({ path: "test-results/chat-task-refs-after.png" });

    await pill171.click();
    // The task card opens in Projects, with the task's number and title. The
    // dev server compiles /projects on the first visit, so the wait is long.
    const card = page.getByRole("heading", { name: "Composer always steers" });
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(page).toHaveURL(/\/projects/);
    await page.screenshot({ path: "test-results/chat-task-refs-opened.png" });
  });
});
