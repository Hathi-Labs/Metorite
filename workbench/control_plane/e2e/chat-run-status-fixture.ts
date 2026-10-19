import { expect, type Page } from "@playwright/test";

/**
 * The stub "server" of the run-status suite (`chat-run-status.spec.ts`) and
 * its visual rig. It lives in `sessionStorage`, so it outlives a reload, as
 * the gateway does. See the spec for the contract it holds.
 */


export type Srv = {
  threadId: string | null;
  /** The server's run: none, running, or a question that waits. */
  state: "none" | "running" | "needs_input";
  live: boolean;
  startedAt: number;
  /** When the run ends, epoch ms. 0: it runs until the test ends it. */
  endsAt: number;
  /** The stream closes on its own after this many ms, with no terminal
   *  event (the proxy's budget). 0: never. */
  cutAfterMs: number;
  /** A second run that started ELSEWHERE (another tab, a phone, a room
   *  member) at this time, epoch ms. 0: none. Its prompt is saved on the
   *  server, and this page never sent it. */
  r2At: number;
  /** An older gateway: no `runId` on a row. */
  noRunId: boolean;
  /** The second register of an older gateway: after this many lists, the
   *  row's startedAt moves by `shiftMs`. 0: never. */
  shiftAfterLists: number;
  shiftMs: number;
  lists: number;
  /** The run asks a question (ask_user) after its first words, waits for the
   *  answer through respond-input, then goes on. */
  ask: boolean;
  /** The answer reached respond-input. */
  answered: boolean;
  userId?: string;
  replyId?: string;
};

export const REPLY = "Here is the full tree of destinations.";
/** The run started elsewhere: its prompt, its first words and its answer. */
export const R2_PROMPT = "Asked from the phone: list the open orders";
export const R2_HEAD = "Reading the open orders now.";
export const R2_REPLY = "There are four open orders.";
/** The question of an asking run, and the words after its answer. */
export const ASK_ID = "q".repeat(32);
export const ASK_NEXT = "Thanks, I will use Apollo.";
export const ASKING = /Asking /;

export async function install(page: Page, initial: Partial<Srv> = {}) {
  await page.addInitScript(({ initial, reply, R2_PROMPT, R2_HEAD, R2_REPLY, ASK_ID, ASK_NEXT }) => {
    // The first load only: a reload keeps the chat and the "server".
    if (!sessionStorage.getItem("__inited")) {
      window.localStorage.clear();
      sessionStorage.setItem("__inited", "1");
      sessionStorage.setItem("__srv", JSON.stringify({
        threadId: null, state: "none", live: true, startedAt: 0, endsAt: 0, cutAfterMs: 0, r2At: 0, noRunId: false, shiftAfterLists: 0, shiftMs: 0, lists: 0, ask: false, answered: false, ...initial,
      }));
    }
    const w = window as unknown as Record<string, unknown>;
    const read = () => JSON.parse(sessionStorage.getItem("__srv") || "{}");
    const write = (patch: Record<string, unknown>) =>
      sessionStorage.setItem("__srv", JSON.stringify({ ...read(), ...patch }));
    w.__srv = { read, write };
    const calls: string[] = [];
    w.__calls = calls;
    const active = () => {
      const s = read();
      if (!s.threadId || s.state === "none") return false;
      return !s.endsAt || Date.now() < s.endsAt;
    };

    // (g): record whether a working row or a live thinking container EVER drew.
    const seen = { working: 0, thinking: 0 };
    w.__seen = seen;
    new MutationObserver(() => {
      if (document.querySelector('[data-run-tone="working"]')) seen.working++;
      if (document.querySelector(".chat-shimmer-text")) seen.thinking++;
    }).observe(document, { subtree: true, childList: true, attributes: true });

    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const r2 = () => !!read().r2At;
    const head = () => [
      { type: "message_start", messageId: "m-1" },
      { type: "delta", content: r2() ? R2_HEAD : "Let me get the rest of the tree.", messageId: "m-1" },
      { type: "message_end", messageId: "m-1" },
      { type: "tool_start", id: "t1", name: "call_agent", args: {} },
      { type: "tool_args", id: "t1", args: { agent: "projects-assistant", message: "List the tree" } },
    ];
    const tail = () => [
      { type: "tool_end", id: "t1", name: "call_agent", args: { agent: "projects-assistant" }, result: "ok", success: true },
      { type: "message_start", messageId: "m-2" },
      { type: "delta", content: r2() ? R2_REPLY : reply, messageId: "m-2" },
      { type: "message_end", messageId: "m-2" },
      { type: "done" },
    ];
    const runStream = (signal?: AbortSignal | null) => new ReadableStream<Uint8Array>({
      async start(controller) {
        for (const e of head()) controller.enqueue(frame(e));
        if (read().ask) {
          // ask_user parks the run on a Future: nothing comes until the answer.
          controller.enqueue(frame({ type: "custom", name: "user_input_requested", value: {
            request_id: ASK_ID, question: "Which project should I use?", choices: ["Apollo", "Gemini"], allowFreeform: false,
          } }));
          while (!read().answered && active()) {
            if (signal?.aborted) { controller.error(new DOMException("The operation was aborted.", "AbortError")); return; }
            await new Promise((r) => setTimeout(r, 100));
          }
          // The run goes on: the ask tool ends, and the next words stream.
          controller.enqueue(frame({ type: "message_start", messageId: "m-ask" }));
          controller.enqueue(frame({ type: "delta", content: ASK_NEXT, messageId: "m-ask" }));
        }
        const opened = Date.now();
        const cut = read().cutAfterMs;
        // Silent until the run ends, or until the "proxy" cuts it.
        while (active()) {
          // An aborted fetch errors its body, as a browser's does.
          if (signal?.aborted) { controller.error(new DOMException("The operation was aborted.", "AbortError")); return; }
          if (cut && Date.now() - opened >= cut) { controller.close(); return; }
          await new Promise((r) => setTimeout(r, 100));
        }
        if (read().state === "needs_input") { controller.close(); return; }
        for (const e of tail()) controller.enqueue(frame(e));
        write({ state: "none" });
        controller.close();
      },
    });

    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/chat/active-sessions")) {
        const s = read();
        if (active()) write({ lists: (s.lists || 0) + 1 });
        const shift = s.shiftAfterLists && (s.lists || 0) >= s.shiftAfterLists ? s.shiftMs : 0;
        return json(active() ? [{
          threadId: s.threadId, agentName: "orchestrator", title: "Plan the release",
          startedAt: new Date((s.r2At || s.startedAt) + shift).toISOString(), state: s.state, live: s.live,
          ...(s.noRunId ? {} : { runId: s.r2At ? "run-2" : "run-1" }),
          askKind: s.state === "needs_input" ? "questions" : null,
          lastStep: s.state === "running" ? "Asking Projects assistant" : null,
        }] : []);
      }
      if (url.includes("/api/chat/pending-asks")) return json([]);
      if (url.includes("/api/agent/respond-input")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const w2 = window as unknown as { __answers?: unknown[] };
        (w2.__answers ??= []).push(body);
        // The second answer to one question: no question waits on that id.
        if (read().answered) return json({ detail: "no question waits on that id" }, 409);
        write({ answered: true });
        return json({ ok: true });
      }
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
        // A solo chat: not a room, and still open to the member (a 404 would
        // read as "not open to you" and open a new chat on reload).
        if (url.includes("/room")) return json({ detail: "not a room" }, 403);
        // The gateway saves the reply at the run's end (its fold), so a page
        // that comes back after the end reads it from the server.
        const s = read();
        if (method === "GET" && url.includes("/messages") && s.threadId && url.includes(s.threadId) && s.r2At) {
          // The run started elsewhere saved its prompt at run start (WS-51 S4).
          return json([
            { id: s.userId, role: "user", content: "Plan the release", timestamp: s.startedAt },
            { id: s.replyId, role: "assistant", content: reply, timestamp: s.startedAt + 1, toolEvents: [] },
            { id: "u-r2", role: "user", content: R2_PROMPT, timestamp: s.r2At },
          ]);
        }
        // Mid-run, the server holds the turn (WS-51 S4) and the reply's last
        // checkpoint, under their own ids, as the gateway does.
        if (method === "GET" && url.includes("/messages") && s.threadId && url.includes(s.threadId) && active() && s.replyId) {
          return json([
            { id: s.userId, role: "user", content: "Plan the release", timestamp: s.startedAt },
            { id: s.replyId, role: "assistant", content: "Let me get the rest of the tree.", timestamp: s.startedAt + 1, toolEvents: [] },
          ]);
        }
        if (method === "GET" && url.includes("/messages") && s.threadId && url.includes(s.threadId) && s.endsAt && !active()) {
          return json([
            { id: s.userId, role: "user", content: "Plan the release", timestamp: s.startedAt },
            { id: s.replyId, role: "assistant", content: reply, timestamp: s.startedAt + 1, toolEvents: [] },
          ]);
        }
        return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
      }
      if (url.includes("/api/agent/cancel")) { write({ state: "none" }); return json({ ok: true }); }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) {
          calls.push("reconnect");
          if (!read().threadId) return json({}, 404);
          if (!active()) {
            // The run ended while the page was away: the replay is whole.
            return new Response(new ReadableStream<Uint8Array>({
              start(c) { for (const e of [...head(), ...tail()]) c.enqueue(frame(e)); c.close(); },
            }), { status: 200, headers: { "content-type": "text/event-stream" } });
          }
          return new Response(runStream(init?.signal), { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        calls.push("send");
        if (!read().threadId || read().state === "none") {
          write({
            threadId: body.threadId, state: "running", startedAt: Date.now(),
            userId: body.userMessageId, replyId: body.assistantMessageId,
          });
        }
        return new Response(runStream(init?.signal), { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { initial, reply: REPLY, R2_PROMPT, R2_HEAD, R2_REPLY, ASK_ID, ASK_NEXT });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

export const srv = (page: Page) => page.evaluate(() => (window as unknown as { __srv: { read: () => Srv } }).__srv.read());
export const setSrv = (page: Page, patch: Partial<Srv>) =>
  page.evaluate((p) => (window as unknown as { __srv: { write: (x: unknown) => void } }).__srv.write(p), patch);
export const composerRow = (page: Page) => page.locator('[data-run-status="composer"]');

export async function openChat(page: Page) {
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  return box;
}

export async function startRun(page: Page) {
  const box = await openChat(page);
  await box.fill("Plan the release");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(composerRow(page)).toContainText(ASKING, { timeout: 15_000 });
}

/** The status is up and working, and Stop is offered. */
export async function expectWorking(page: Page, timeout = 1_500) {
  await expect(composerRow(page)).toHaveAttribute("data-run-tone", "working", { timeout });
  await expect(page.locator('[data-chat-primary="stop"]')).toBeVisible({ timeout });
}

