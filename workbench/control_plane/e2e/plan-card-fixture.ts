import { expect, type Page } from "@playwright/test";

/**
 * The plan card in the real chat page, with no gateway (owner, 2026-10-18).
 *
 * The stream is held open inside the page, so the card waits as a parked run
 * does. Each answer the card sends lands in `window.__respondBodies`. The
 * spec `plan-card.spec.ts` drives it, and a visual rig can import it too.
 *
 * The plan is the owner's example: eight tasks in two phases, two of them
 * unassigned, three of them marked.
 */

export const PLAN_NAME = "Personalization & Memory System";

export const PLAN_TASKS = [
  {
    key: "t1", title: "Build the memory store and write path", owner: "ayush@fracktal.in",
    effort_mins: 240, start: "2026-10-22", due: "2026-10-24", phase: "Foundation",
    fit: "Python, Postgres", hours: "fits, 6 h spare before the plan", marks: [], warnings: [],
    impact: 4, urgency: 3, effort: 2, priority: 24,
  },
  {
    key: "t2", title: "Design the recall API", owner: "", effort_mins: 120,
    start: "2026-10-23", due: "2026-10-23", phase: "Foundation", marks: [], warnings: [],
  },
  {
    key: "t3", title: "Define the memory schema and the retention rules", owner: "nithin@fracktal.in",
    effort_mins: 360, start: "2026-10-22", due: "2026-10-23", phase: "Foundation",
    fit: "Postgres", hours: "short 4 h by 2026-10-23: needs 10 h, has 6 h",
    marks: ["short of hours"], warnings: [],
  },
  {
    key: "t4", title: "Add embeddings to the write path", owner: "ayush@fracktal.in", effort_mins: 480,
    start: "2026-10-26", due: "2026-10-28", after: ["t1"], phase: "Foundation",
    fit: "Python", hours: "short 2 h by 2026-10-28: needs 18 h, has 16 h",
    marks: ["short of hours"], warnings: [],
  },
  {
    key: "t5", title: "Load test the retrieval path", owner: "nithin@fracktal.in", effort_mins: 480,
    start: "2026-10-27", due: "2026-10-28", after: ["t4"], phase: "Rollout",
    fit: "no skill matched", hours: "fits", marks: ["no skill match"],
    warnings: ["Load testing is not on the owner's skills."],
  },
  {
    key: "t6", title: "Personalize the chat answers from memory", owner: "priya.sharma@fracktal.in",
    effort_mins: 720, start: "2026-11-02", due: "2026-11-06", after: ["t2"], phase: "Rollout",
    fit: "TypeScript, prompts", hours: "fits, 12 h spare before the plan", marks: [], warnings: [],
  },
  {
    key: "t7", title: "Write the privacy and deletion controls", owner: "", effort_mins: 240,
    start: "2026-11-05", due: "2026-11-07", phase: "Rollout", marks: [], warnings: [],
  },
  {
    key: "t8", title: "Ship behind a flag and review the metrics", owner: "priya.sharma@fracktal.in",
    effort_mins: 120, start: "2026-11-12", due: "2026-11-15", after: ["t6", "t7"], phase: "Rollout",
    fit: "TypeScript", hours: "fits", marks: [], warnings: [],
  },
];

export const PLAN_DATA = {
  title: `Plan · ${PLAN_NAME}`,
  description: "Under Metorite.",
  project: { name: PLAN_NAME, parent: "Metorite", description: "" },
  tasks: PLAN_TASKS,
  capacity: "Hours cover 2026-10-18 to 2026-11-15, across all your visible work.",
  warnings: [
    "Personalize the chat answers from memory starts on 2026-11-02, but Design the recall API, which blocks it, runs until 2026-10-23. Both dates stay as planned.",
  ],
  risks: [
    "Recall is slow under load, and the chat waits on it.",
    "Members do not trust what the assistant remembers.",
    "The deletion controls slip, and the launch waits on legal.",
  ],
  submitLabel: "Review plan",
};

/** A card above the plan in the same turn: the neighbour for the one-look check. */
const NEIGHBOUR = {
  type: "template",
  props: {
    name: "dataGrid",
    data: {
      title: "Open work for the memory team",
      columns: ["Task", "Owner", "Status", "Due"],
      rows: [
        { cells: ["Fix the search ranking", "Ayush", "In progress", "2026-10-20"] },
        { cells: ["Review the vector index", "Nithin", "To do", "2026-10-21"] },
        { cells: ["Draft the memory FAQ", "Priya", "Backlog", "2026-10-30"] },
      ],
    },
  },
};

export async function installPlanScenario(page: Page, opts: { neighbour?: boolean; data?: unknown } = {}) {
  await page.addInitScript(
    ({ plan, neighbour }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const respondBodies: unknown[] = [];
      w.__respondBodies = respondBodies;
      let release: () => void = () => {};
      const released = new Promise<void>((r) => { release = r; });
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      const orig = window.fetch.bind(window);
      let turns = 0;
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/api/agent/respond-input")) {
          respondBodies.push(JSON.parse(String(init?.body ?? "{}")));
          setTimeout(release, 800);
          return json({ ok: true });
        }
        if (url.includes("/api/chat/active-sessions")) return json([]);
        if (url.includes("/api/auth/me")) {
          return json({
            email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat"],
            features_denied: [], agents: ["orchestrator"], permissions: ["*"],
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
          turns += 1;
          const first = turns === 1;
          const stream = new ReadableStream<Uint8Array>({
            async start(controller) {
              if (!first) {
                controller.enqueue(frame({ type: "message_start", messageId: "m-9" }));
                controller.enqueue(frame({ type: "delta", content: "Noted.", messageId: "m-9" }));
                controller.enqueue(frame({ type: "done" }));
                controller.close();
                return;
              }
              controller.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              controller.enqueue(frame({ type: "delta", content: "Here is a plan for the memory system.", messageId: "m-1" }));
              controller.enqueue(frame({ type: "message_end", messageId: "m-1" }));
              if (neighbour) {
                controller.enqueue(frame({ type: "tool_start", id: "t-grid", name: "emit_generative_ui", args: {} }));
                controller.enqueue(frame({ type: "custom", name: "generative_ui", value: neighbour }));
                controller.enqueue(frame({ type: "tool_end", id: "t-grid", name: "emit_generative_ui", success: true, result: "{\"ok\":true}" }));
              }
              controller.enqueue(frame({ type: "tool_start", id: "t-plan", name: "emit_generative_ui", args: {} }));
              controller.enqueue(frame({
                type: "custom",
                name: "generative_ui",
                value: { type: "template", props: { name: "planCard", data: plan }, request_id: "req-plan-1" },
              }));
              await released;
              controller.enqueue(frame({ type: "tool_end", id: "t-plan", name: "emit_generative_ui", success: true, result: "{\"ok\":true}" }));
              controller.enqueue(frame({ type: "message_start", messageId: "m-2" }));
              controller.enqueue(frame({ type: "delta", content: "Thanks. A confirmation card follows.", messageId: "m-2" }));
              controller.enqueue(frame({ type: "message_end", messageId: "m-2" }));
              controller.enqueue(frame({ type: "done" }));
              controller.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { plan: opts.data ?? PLAN_DATA, neighbour: opts.neighbour ? NEIGHBOUR : null },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

/** Open the chat, send one message, and wait for the plan card. */
export async function startPlanTurn(page: Page) {
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Plan the memory system");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-plan-card]").first()).toBeVisible({ timeout: 20_000 });
}

export const respondBodies = (page: Page) =>
  page.evaluate(() => (window as unknown as { __respondBodies: Array<Record<string, unknown>> }).__respondBodies);
