import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { CONTEXTS } from "./harness";
import { openAllRows } from "../railOpen";

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
  await page.route("**/api/projects/needs-attention**", (r) =>
    r.fulfill({ json: { rows: [ATTN_ROW], total: 1, counts: { stalled: 1, dangling: 0, blocked: 0 }, listed: 1, offset: 0, limit: 50, capped: false } }));
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


const OUT = path.resolve(__dirname, "../../../../.captures_2549");
const WANT = ["dark-1440", "light-1440", "compact-1440", "accent-violet-1440"];

async function shoot(page: Page, name: string, target: ReturnType<Page["locator"]>) {
  for (const ctx of CONTEXTS.filter((c) => WANT.includes(c.name))) {
    await page.setViewportSize({ width: ctx.width, height: ctx.height });
    if (ctx.apply) await ctx.apply(page);
    await page.waitForTimeout(500);
    await target.first().scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: path.join(OUT, `${name}-${ctx.name}.png`) });
    if (ctx.undo) await ctx.undo(page);
  }
}

test("2549: Release dialog in the run panel", async ({ page }) => {
  test.setTimeout(240_000);
  await stub(page, "working");
  await openTask(page);
  const panel = page.locator("[data-agent-run-panel]");
  await panel.locator("[data-agent-release]").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await shoot(page, "release-dialog", dialog);
});

const ATTN_ROW = {
  kind: "stalled", task_id: "t2", task_number: 2, title: TASK.title, project_id: "p2", project_name: "Bootloader",
  parent_task_id: null, category: "in_progress", archived: false, owner: ME, owner_name: "You",
  since: "2026-11-18T10:00:00Z", age_hours: 30, run_id: "r1", run_status: "in_progress",
  client_via: "mcp:claude.ai:claude-code", client_name: "Claude Code", reason: "no_activity",
  blocker_id: null, blocker_number: null, blocker_title: null, action: "close_run",
  actions: ["close_run"], can_act: true, yours: true, connection_gone: true, can_resume: false,
};

async function attentionDialog(page: Page) {
  await stub(page, "working");
  await page.getByRole("button", { name: /Needs attention/ }).first().click();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "Board", exact: true }).first().click();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: /Needs attention/ }).first().click();
  const btn = page.locator('[data-attention-action="close_run"]').first();
  await expect(btn).toBeVisible({ timeout: 20_000 });
  await btn.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test("2549: Close the run dialog in Needs attention", async ({ page }) => {
  test.setTimeout(240_000);
  const dialog = await attentionDialog(page);
  await shoot(page, "close-run-dialog", dialog);
});

test("2549: both dialogs at 390 px", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await attentionDialog(page);
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "close-run-dialog-mobile-390.png") });
  void dialog;
});
