import { expect, test, type Page } from "@playwright/test";
import { openAllRows } from "./railOpen";

/**
 * WS-55 EA-12c — an outside agent's run on a task (`external_agent_access.md`
 * §12.1).
 *
 * The routes of EA-12a are mocked through `page.route`, in the shapes of the
 * spec. Three checks:
 *
 * 1. The answer card draws the agent's options, with the recommended one
 *    marked.
 * 2. An answer sends the option id, collapses the card to "Answered by …",
 *    and the pill reads "Answered".
 * 3. Stop asks first, then sends a `prompt` with the signal `stop`.
 * 4. A 409 (another member answered first) reads the run again, with no
 *    "Try again" (fix round 1).
 * 5. EA-12d, "Ask an agent": the control is gone while the routes answer
 *    404. A member picks a connection and sends, and the run reads "Queued
 *    for Claude Code". With no connected agent, the dialog links to the AI
 *    agents view. A 409 shows the server's text.
 * 6. EA-6g, the archive request: only the run's member sees Approve and
 *    Decline, and the decision goes to the approval route. Every other
 *    member reads "Waiting for <name>". An expired request is no dead
 *    end: its member may Decline it, and the reply box comes back.
 *    Mutations that turned these red: P1 `archiveMine = true` (the waiting
 *    test), P2 the panel answers through the generic route (the approve
 *    test), and P3 the generic card draws for a typed request (the waiting
 *    test). Fix round 1: P4 the expired state has no Decline (the decline
 *    test), and P5 the reply box stays away after the expiry (the expired
 *    test of another member).
 *
 * Run it by name:
 *
 *     npx playwright test e2e/agent-runs.spec.ts --project=chromium
 */

const STATUSES = [
  { id: "s2", project_id: "p2", name: "To do", color: "gray", position: 20, category: "todo", is_default: true },
  { id: "s3", project_id: "p2", name: "In progress", color: "blue", position: 30, category: "in_progress", is_default: false },
];

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

const ME = "you@example.com";

const TASK = {
  id: "t2",
  project_id: "p2",
  root_project_id: "p1",
  task_number: 2,
  status_id: "s3",
  title: "Port the migration runner",
  tags: [],
  assignees: [ME],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
};

/** A task with no agent run, which "Waiting on me" must hide. */
const OTHER = { ...TASK, id: "t3", task_number: 3, title: "Write the release notes", agent_state: null };

const ELICITATION = {
  id: "a1",
  seq: 2,
  type: "elicitation",
  content: { body: "Which database should the migration target?" },
  signal: "select",
  signal_metadata: {
    options: [
      { id: "pg", label: "Postgres", recommended: true },
      { id: "sqlite", label: "SQLite" },
    ],
    allow_multiple: false,
  },
  actor: ME,
  actor_via: "mcp:claude.ai:claude-code",
  in_reply_to: null,
  created_at: "2026-10-14T10:00:00Z",
};

const THOUGHT = {
  id: "a0",
  seq: 1,
  type: "thought",
  content: { body: "Reading the runner" },
  signal: null,
  signal_metadata: null,
  actor: ME,
  actor_via: "mcp:claude.ai:claude-code",
  in_reply_to: null,
  created_at: "2026-10-14T09:59:00Z",
};

function run(status: string, state: string) {
  return {
    id: "r1",
    task_id: "t2",
    member_email: ME,
    client_via: "mcp:claude.ai:claude-code",
    status,
    state,
    started_at: "2026-10-14T09:58:00Z",
    ended_at: null,
    external_link: "https://claude.ai/code/session-1",
    last_activity_at: "2026-10-14T10:00:00Z",
  };
}

/** EA-6g: an archive request, typed by the server. The stamp is far ahead. */
function archiveRequest(expired = false) {
  return {
    id: "a7",
    seq: 2,
    type: "elicitation",
    content: {
      body: "The agent asks to archive this task. The subtasks stay where they are. Only the member of this agent run can approve or decline.",
    },
    content_metadata: {
      request: {
        kind: "archive_task",
        expires_at: expired ? "2020-01-01T00:00:00Z" : "2999-01-01T00:00:00Z",
      },
    },
    signal: "select",
    signal_metadata: {
      options: [
        { id: "approve", label: "Approve" },
        { id: "decline", label: "Decline" },
      ],
      allow_multiple: false,
    },
    actor: ME,
    actor_via: "mcp:claude.ai:claude-code",
    in_reply_to: null,
    created_at: "2026-10-14T10:00:00Z",
  };
}

/** EA-12d: the member's instruction, the first `prompt` of a queued run. */
const PROMPT = {
  id: "a5",
  seq: 1,
  type: "prompt",
  content: { body: "Write the tests for the runner." },
  signal: null,
  signal_metadata: null,
  actor: ME,
  actor_via: null,
  in_reply_to: null,
  created_at: "2026-10-14T10:05:00Z",
};

/** EA-12d: one live connection of the member, with the write scope. */
const CONNECTION = {
  id: "c1",
  member_email: ME,
  client_name: "Claude Code",
  client_origin: "https://claude.ai",
  scopes: ["projects:read", "projects:write"],
  pinned_projects: [],
  include_personal: false,
  created_at: "2026-10-10T09:00:00Z",
  last_used_at: null,
  expires_at: "2026-12-10T09:00:00Z",
};

/** EA-12d: the gateway's 409 when that agent has not picked up its last run. */
const START_409 = "The agent has not picked this run up yet. Wait for it to start, then reply.";

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

/**
 * Answers every call. `start` is the run's first status. The test reads
 * `posts` for what the panel wrote.
 */
async function stub(
  page: Page,
  start: "awaiting" | "working" | "off" | "none",
  opts: {
    loseTheRace?: boolean;
    connections?: unknown[];
    startStatus?: number;
    /** EA-6g: the open question is an archive request of this member's run. */
    archive?: { member?: string; expired?: boolean };
  } = {},
) {
  const posts: Record<string, unknown>[] = [];
  /** EA-6g: the bodies of each archive approval. */
  const approvals: Record<string, unknown>[] = [];
  let decided = "approve";
  /** EA-12d: the bodies of each "Ask an agent" start. */
  const starts: Record<string, unknown>[] = [];
  /** EA-12d: `none` has no run until the member asks an agent. */
  let queued = false;
  let answered = false;
  // Fix round 1, item 9: another member answers first, so the member's POST
  // gets 409 and the run now holds the other member's answer.
  let answeredBy = ME;

  const question = opts.archive ? archiveRequest(opts.archive.expired) : ELICITATION;
  const runOf = (status: string, state: string) => ({
    ...run(status, state),
    member_email: opts.archive?.member ?? ME,
  });
  const current = () => {
    if (answered) {
      return {
        run: runOf("in_progress", "answered"),
        activities: [
          THOUGHT,
          question,
          {
            id: "a3",
            seq: 3,
            type: "prompt",
            content: opts.archive
              ? { selected: [decided], archived: decided === "approve" }
              : { selected: ["pg"] },
            signal: "continue",
            signal_metadata: null,
            actor: answeredBy,
            actor_via: null,
            in_reply_to: question.id,
            created_at: "2026-10-14T10:01:00Z",
          },
        ],
      };
    }
    if (queued) return { run: runOf("created", "queued"), activities: [PROMPT] };
    return start === "awaiting"
      ? { run: runOf("awaiting", "awaiting"), activities: [THOUGHT, question] }
      : { run: runOf("in_progress", "working"), activities: [THOUGHT] };
  };
  const hasRun = () => start !== "off" && (start !== "none" || queued);
  const taskNow = () => ({ ...TASK, agent_state: hasRun() ? current().run.state : null });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\//, "");

    // The flag `agent_runs_enabled` off: every agent-run route answers 404.
    if (start === "off" && path.includes("agent-runs")) {
      return route.fulfill({ status: 404, json: { detail: "Not Found" } });
    }
    if (request.method() === "POST" && path === "projects/agent-runs/r1/activities") {
      const body = request.postDataJSON() as Record<string, unknown>;
      posts.push(body);
      if (Array.isArray(body.selected) && opts.loseTheRace) {
        answered = true;
        answeredBy = "priya@example.com";
        return route.fulfill({
          status: 409,
          json: { detail: "This question already has an answer." },
        });
      }
      if (Array.isArray(body.selected)) answered = true;
      return route.fulfill({ json: { id: "a9", status: "in_progress" } });
    }
    if (request.method() === "POST" && path === "projects/agent-runs/r1/archive-approval") {
      const body = request.postDataJSON() as Record<string, unknown>;
      approvals.push(body);
      decided = String(body.decision);
      answered = true;
      return route.fulfill({
        json: {
          activity: null,
          run: current().run,
          created: true,
          archived: decided === "approve",
          task: decided === "approve" ? { ...TASK, archived_at: "2026-10-14T10:02:00Z" } : null,
        },
      });
    }
    if (request.method() === "POST" && path === "projects/tasks/t2/agent-runs") {
      starts.push(request.postDataJSON() as Record<string, unknown>);
      if (opts.startStatus === 409) {
        return route.fulfill({ status: 409, json: { detail: START_409 } });
      }
      queued = true;
      return route.fulfill({ status: 201, json: { run: current().run, created: true } });
    }
    if (request.method() !== "GET") return route.fulfill({ json: EMPTY });

    // EA-4b: the member's own agent connections.
    if (path === "agent-access/connections") {
      return route.fulfill({ json: { connections: opts.connections ?? [CONNECTION] } });
    }
    if (path === "projects/agent-runs/r1") {
      // The cursor: only what is newer than `after`.
      const after = Number(url.searchParams.get("after") ?? "0");
      const now = current();
      return route.fulfill({
        json: { ...now, activities: now.activities.filter((a) => a.seq > after) },
      });
    }
    if (path === "projects/tasks/t2/agent-runs") {
      return route.fulfill({ json: { rows: hasRun() ? [current().run] : [] } });
    }
    if (path === "projects/agent-runs") return route.fulfill({ json: { rows: [] } });
    if (path === "projects/tasks/t2") return route.fulfill({ json: taskNow() });
    if (path === "projects/tasks") return route.fulfill({ json: { rows: [taskNow(), OTHER], total: 2 } });
    if (path.includes("nodes/p2/statuses")) {
      return route.fulfill({ json: { rows: STATUSES, total: STATUSES.length } });
    }
    if (path.includes("projects/tree")) return route.fulfill({ json: { rows: TREE, total: 1 } });
    if (path.includes("auth/me")) return route.fulfill({ json: { email: ME, name: "You" } });
    return route.fulfill({ json: EMPTY });
  });

  // The lost race pauses the page clock later, so the poll cannot do the
  // re-read that the 409 must do. Time flows normally until then.
  if (opts.loseTheRace) await page.clock.install();
  await page.goto("/projects");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(4000);
  await openAllRows(page);
  await page.getByText("Bootloader", { exact: true }).first().click();
  await page.waitForTimeout(2500);
  return { posts, starts, approvals };
}

/** Open the task's panel from its row or card. */
async function openTask(page: Page) {
  await page.getByText(TASK.title, { exact: true }).first().click();
  await expect(page.locator("[data-agent-run-panel]")).toBeVisible({ timeout: 15_000 });
}

test("the card renders the agent's options, with the recommended one marked", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, "awaiting");

  // The row's pill, before the task opens.
  await expect(page.locator('[data-agent-pill="awaiting"]').first()).toBeVisible();

  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  await expect(panel.getByText("Which database should the migration target?")).toBeVisible();
  await expect(panel.locator("[data-elicitation-option]")).toHaveCount(2);
  await expect(panel.getByText("Recommended", { exact: true })).toBeVisible();
  await expect(panel.getByRole("link", { name: /claude\.ai/ })).toBeVisible();
});

test("an answer collapses the card, and the pill reads Answered", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts } = await stub(page, "awaiting");
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");

  await panel.locator("[data-elicitation-option]").filter({ hasText: "Postgres" }).click();
  await panel.locator("[data-elicitation-submit]").click();

  await expect(panel.locator("[data-agent-answered]")).toHaveText(/Answered by you: Postgres/i, {
    timeout: 10_000,
  });
  await expect(panel.locator("[data-elicitation-card]")).toHaveCount(0);
  await expect(page.locator('[data-agent-pill="answered"]').first()).toContainText("Answered");

  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({ type: "prompt", selected: ["pg"] });
  expect(typeof posts[0].idempotency_key).toBe("string");
});

test("a lost race reads the run again and offers no retry", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts } = await stub(page, "awaiting", { loseTheRace: true });
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  // No timer fires from here on, so only the 409's own re-read can show
  // the other member's answer. The poll every 5 s cannot.
  await page.clock.pauseAt((await page.evaluate(() => Date.now())) + 1_000);

  await panel.locator("[data-elicitation-option]").filter({ hasText: "Postgres" }).click();
  await panel.locator("[data-elicitation-submit]").click();

  await expect(panel.getByText("This question already has an answer.")).toBeVisible({
    timeout: 10_000,
  });
  await expect(panel.getByText(/Try again/)).toHaveCount(0);
  // The re-read shows the other member's answer, and the card closes.
  await expect(panel.locator("[data-agent-answered]")).toHaveText(/Answered by priya: Postgres/i, {
    timeout: 10_000,
  });
  await expect(panel.locator("[data-elicitation-card]")).toHaveCount(0);
  expect(posts).toHaveLength(1);
});

test("Stop asks first, then sends the stop signal", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts } = await stub(page, "working");
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  await expect(panel.locator("[data-agent-ephemeral]")).toHaveText("Reading the runner");

  await panel.getByRole("button", { name: "Stop agent" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Stop the agent?")).toBeVisible();
  expect(posts).toHaveLength(0);
  await dialog.getByRole("button", { name: "Stop agent" }).click();

  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ type: "prompt", signal: "stop" });
});

test("with the routes off, the task shows no panel and no filter", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, "off");
  await expect(page.getByRole("button", { name: "Watching" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Waiting on me" })).toHaveCount(0);
  await page.getByText(TASK.title, { exact: true }).first().click();
  await expect(page.getByRole("button", { name: "Comment", exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-agent-run-panel]")).toHaveCount(0);
  await expect(page.locator("[data-agent-pill]")).toHaveCount(0);
  // EA-12d: no "Ask an agent" while the routes answer 404.
  await expect(page.locator("[data-ask-agent]")).toHaveCount(0);
});

test("Waiting on me keeps the tasks whose agent waits for me", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, "awaiting");
  const filter = page.getByRole("button", { name: "Waiting on me" });
  await expect(filter).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(OTHER.title, { exact: true })).toHaveCount(1);
  await filter.click();
  await expect(filter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(TASK.title, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(OTHER.title, { exact: true })).toHaveCount(0);
});

test("Waiting on me with nothing waiting says so, not Drop here", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, "working");
  const filter = page.getByRole("button", { name: "Waiting on me" });
  await expect(filter).toBeVisible({ timeout: 15_000 });
  await filter.click();
  await expect(page.getByText("Nothing waits on you").first()).toBeVisible();
  await expect(page.getByText("Drop here")).toHaveCount(0);
});

/* ── EA-12d: Ask an agent ──────────────────────────────────────────────── */

/** Open the task that has no run. Its detail shows only the Ask control. */
async function openAskable(page: Page) {
  await page.getByText(TASK.title, { exact: true }).first().click();
  await expect(page.locator("[data-ask-agent]")).toBeVisible({ timeout: 15_000 });
}

test("Ask an agent: pick a connection, send, and the run reads Queued", async ({ page }) => {
  test.setTimeout(120_000);
  const vscode = {
    ...CONNECTION,
    id: "c2",
    client_name: "VS Code",
    client_origin: "https://vscode.dev",
  };
  const { starts } = await stub(page, "none", { connections: [vscode, CONNECTION] });
  await openAskable(page);
  await expect(page.locator("[data-agent-run-panel]")).toHaveCount(0);

  await page.locator("[data-ask-agent]").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Ask an agent").first()).toBeVisible();
  const send = dialog.getByRole("button", { name: "Send to agent" });
  // The instruction is required.
  await expect(send).toBeDisabled();
  // The first connection is picked for the member, who picks the other.
  const picker = dialog.getByRole("button", { name: "Agent", exact: true });
  await expect(picker).toContainText("VS Code (vscode.dev)");
  await picker.click();
  await page.getByRole("option", { name: "Claude Code (claude.ai)" }).click();
  await expect(picker).toContainText("Claude Code (claude.ai)");
  await dialog.getByRole("textbox").fill("Write the tests for the runner.");
  await send.click();

  await expect(dialog).toHaveCount(0, { timeout: 10_000 });
  const panel = page.locator("[data-agent-run-panel]");
  await expect(panel).toBeVisible();
  await expect(panel.locator('[data-agent-pill="queued"]')).toHaveText("Queued for Claude Code");
  // The caller now has an open run, so the task offers no second ask.
  await expect(page.locator("[data-ask-agent]")).toHaveCount(0);

  expect(starts).toHaveLength(1);
  expect(starts[0]).toMatchObject({ connection_id: "c1", body: "Write the tests for the runner." });
  expect(typeof starts[0].idempotency_key).toBe("string");
});

test("Ask an agent with no connected agent links to the AI agents view", async ({ page }) => {
  test.setTimeout(120_000);
  // A read-only connection cannot pick a run up, so it is not offered.
  const { starts } = await stub(page, "none", {
    connections: [{ ...CONNECTION, scopes: ["projects:read"] }],
  });
  await openAskable(page);
  await page.locator("[data-ask-agent]").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator("[data-ask-agent-empty]")).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Connect an agent" })).toHaveAttribute(
    "href",
    "/integrations?view=agents",
  );
  await expect(dialog.getByRole("button", { name: "Send to agent" })).toHaveCount(0);
  expect(starts).toHaveLength(0);
});

test("Ask an agent shows the server's text for a 409", async ({ page }) => {
  test.setTimeout(120_000);
  const { starts } = await stub(page, "none", { startStatus: 409 });
  await openAskable(page);
  await page.locator("[data-ask-agent]").click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill("And this too.");
  await dialog.getByRole("button", { name: "Send to agent" }).click();
  await expect(dialog.getByRole("alert")).toHaveText(START_409);
  await expect(dialog.getByText(/Try again/)).toHaveCount(0);
  expect(starts).toHaveLength(1);
});

/* ── EA-6g: the archive request ────────────────────────────────────────── */

test("the run's member approves an archive request", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts, approvals } = await stub(page, "awaiting", { archive: {} });
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  const card = panel.locator('[data-archive-request="mine"]');
  await expect(card).toBeVisible();
  await expect(card.locator("[data-elicitation-option]")).toHaveCount(2);
  await expect(card.getByText("Archive request", { exact: true })).toBeVisible();
  await expect(card.getByText("Question", { exact: true })).toHaveCount(0);
  await expect(card.getByText("Approve archives this task. Its subtasks stay where they are.")).toBeVisible();
  // The approval takes a decision only: no free text, and no reply box.
  await expect(card.getByRole("textbox")).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: /Reply to/ })).toHaveCount(0);

  await card.locator("[data-elicitation-option]").filter({ hasText: "Approve" }).click();
  await card.locator("[data-elicitation-submit]").click();

  await expect(panel.locator("[data-agent-answered]")).toHaveText(/Answered by you: Approve/i, {
    timeout: 10_000,
  });
  await expect(panel.locator("[data-archive-request]")).toHaveCount(0);
  expect(approvals).toHaveLength(1);
  expect(approvals[0]).toMatchObject({ decision: "approve" });
  expect(typeof approvals[0].idempotency_key).toBe("string");
  // The generic answer route is never used for a request.
  expect(posts).toHaveLength(0);
});

test("another member reads Waiting for the run's member, with no button", async ({ page }) => {
  test.setTimeout(120_000);
  const { posts, approvals } = await stub(page, "awaiting", {
    archive: { member: "priya@example.com" },
  });
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  const waiting = panel.locator('[data-archive-request="waiting"]');
  await expect(waiting).toBeVisible();
  await expect(waiting.locator("[data-archive-waiting]")).toHaveText("Waiting for Priya");
  await expect(waiting.getByText(/asks to archive this task/)).toBeVisible();
  await expect(panel.locator("[data-elicitation-option]")).toHaveCount(0);
  await expect(panel.locator("[data-elicitation-submit]")).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: /Reply to/ })).toHaveCount(0);
  expect(approvals).toHaveLength(0);
  expect(posts).toHaveLength(0);
});

test("the member declines an expired archive request, and may reply", async ({ page }) => {
  test.setTimeout(120_000);
  const { approvals } = await stub(page, "awaiting", { archive: { expired: true } });
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  const expired = panel.locator('[data-archive-request="expired"]');
  await expect(expired).toContainText("This archive request expired. Nothing was archived.");
  // No approve after the expiry, but the run is no dead end.
  await expect(panel.locator("[data-elicitation-option]")).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: /Reply to/ })).toBeVisible();
  await expired.locator("[data-archive-decline]").click();
  await expect(panel.locator("[data-agent-answered]")).toHaveText(/Answered by you: Decline/i, {
    timeout: 10_000,
  });
  expect(approvals).toHaveLength(1);
  expect(approvals[0]).toMatchObject({ decision: "decline" });
});

test("another member reads an expired request, with no Decline", async ({ page }) => {
  test.setTimeout(120_000);
  const { approvals } = await stub(page, "awaiting", {
    archive: { member: "priya@example.com", expired: true },
  });
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  await expect(panel.locator('[data-archive-request="expired"]')).toBeVisible();
  await expect(panel.locator("[data-archive-decline]")).toHaveCount(0);
  await expect(panel.getByRole("textbox", { name: /Reply to/ })).toBeVisible();
  expect(approvals).toHaveLength(0);
});
