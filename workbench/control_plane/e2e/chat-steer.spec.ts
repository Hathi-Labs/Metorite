import { expect, test, type Page } from "@playwright/test";

/**
 * The owner's request of 2026-10-12, in a real browser against the real chat
 * page: a message sent while the agent works STEERS the live run. The stream
 * goes on, the words show at once as "added to the reply", and there is one
 * primary control: Stop for an empty box, Send for a box with text. There is
 * no send-mode menu and no "stop and send".
 *
 * The run's stream is held inside the page (a `fetch` override) and goes on
 * one step at a time, so each moment can be measured. The steer answers 202
 * `{steered: true}`, as the gateway does (§QM-1). After the run, the chat asks
 * once for the steer follow-up, and this stub answers "nothing pending".
 *
 * The vitest twin is `src/lib/chatSteer.test.ts`. The server's half is
 * `tests/unit/test_steer_follow_up.py`.
 *
 * No gateway runs under this suite. Every request the page makes either has
 * a stub here, or fails at once (`GATEWAY_RETRY_DEADLINE_MS=0`).
 */

const STEER_TEXT = "Also add a due date";

async function install(page: Page, opts: { followUpAnswers?: boolean } = {}) {
  await page.addInitScript(({ steerText, followUpAnswers }) => {
    window.localStorage.clear();
    const w = window as unknown as Record<string, unknown>;
    const calls: Record<string, unknown>[] = [];
    w.__calls = calls;
    // One gate per run: each run's stream waits on its own release.
    let open: (() => void) | null = null;
    w.__release = () => open?.();
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
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
      if (url.includes("/api/agent/cancel")) {
        calls.push({ kind: "cancel" });
        open?.();
        return json({ ok: true });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        if (body.steerFollowUp) {
          calls.push({ kind: "followup", message: body.message });
          if (!followUpAnswers) {
            return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
          }
          // The run never read the steer: the gateway's follow-up answers it.
          return new Response(
            new ReadableStream<Uint8Array>({
              start(c) {
                c.enqueue(frame({ type: "message_start", messageId: "m-2" }));
                c.enqueue(frame({ type: "delta", content: "Done. Each task now has a due date.", messageId: "m-2" }));
                c.enqueue(frame({ type: "message_end", messageId: "m-2" }));
                c.enqueue(frame({ type: "done" }));
                c.close();
              },
            }),
            { status: 200, headers: { "content-type": "text/event-stream" } },
          );
        }
        if (body.message === steerText) {
          calls.push({ kind: "steer", message: body.message });
          return json({ steered: true, delivered: true, pendingReplay: false, reason: "folded_into_live_run" }, 202);
        }
        calls.push({ kind: "run", message: body.message });
        const signal = init?.signal;
        const gate = new Promise<void>((r) => { open = r; });
        const stream = new ReadableStream<Uint8Array>({
          async start(c) {
            c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            c.enqueue(frame({ type: "delta", content: "Working on the plan. ", messageId: "m-1" }));
            await gate;
            if (signal?.aborted) { c.close(); return; }
            c.enqueue(frame({ type: "delta", content: "The due date is Friday.", messageId: "m-1" }));
            c.enqueue(frame({ type: "message_end", messageId: "m-1" }));
            c.enqueue(frame({ type: "done" }));
            c.close();
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { steerText: STEER_TEXT, followUpAnswers: !!opts.followUpAnswers });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

const calls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __calls: { kind: string; message?: string }[] }).__calls);
const release = (page: Page) =>
  page.evaluate(() => (window as unknown as { __release: () => void }).__release());

async function startRun(page: Page, opts: { followUpAnswers?: boolean } = {}) {
  await page.setViewportSize({ width: 1280, height: 860 });
  await install(page, opts);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Plan the release");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Working on the plan.").first()).toBeVisible({ timeout: 15_000 });
  return box;
}

test.describe("a message sent during a run steers it", () => {
  test.setTimeout(120_000);

  test("the stream goes on, the words show as added to the reply, and one control serves", async ({ page }) => {
    const box = await startRun(page);
    const primary = page.locator("[data-chat-primary]");

    // A live run with an empty box: the one control is Stop.
    await expect(primary).toHaveCount(1);
    await expect(primary).toHaveAttribute("data-chat-primary", "stop");
    await expect(page.getByRole("button", { name: "Stop the reply" })).toBeVisible();
    // No send mode, no chevron, no second Stop.
    await expect(page.getByRole("button", { name: /send mode/i })).toHaveCount(0);
    await expect(page.getByText(/Queue|Steer/)).toHaveCount(0);

    // Text in the box: the control is Send, with the short steer cue.
    await box.fill(STEER_TEXT);
    await expect(primary).toHaveAttribute("data-chat-primary", "send");
    await expect(page.locator("[data-steer-caption]")).toHaveText("Adds to the current reply");
    await page.getByRole("button", { name: "Send", exact: true }).click();

    // The words show at once, marked, and the server's answer acknowledges them.
    const steered = page.locator("[data-chat-steer]");
    await expect(steered).toContainText(STEER_TEXT);
    await expect(steered).toHaveAttribute("data-chat-steer", "added");
    await expect(steered).toContainText("Added to the reply");
    await expect(box).toHaveValue("");
    // The run is still live: Stop is back for the empty box, and nothing
    // stopped it.
    await expect(primary).toHaveAttribute("data-chat-primary", "stop");
    expect((await calls(page)).map((c) => c.kind)).toEqual(["run", "steer"]);

    // The SAME stream goes on, into the same reply.
    await release(page);
    await expect(page.getByRole("paragraph").filter({ hasText: "Working on the plan. The due date is Friday." })).toBeVisible();
    await expect(primary).toHaveAttribute("data-chat-primary", "send");
    // Once the run ends, the chat asks once for the steer follow-up. Nothing
    // was owed, so nothing draws.
    await expect.poll(async () => (await calls(page)).map((c) => c.kind)).toEqual(["run", "steer", "followup"]);
    const steer = (await calls(page)).find((c) => c.kind === "steer");
    expect(steer?.message).toBe(STEER_TEXT);
  });

  test("a steer the run never read is answered by the follow-up, below it", async ({ page }) => {
    const box = await startRun(page, { followUpAnswers: true });
    await box.fill(STEER_TEXT);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.locator("[data-chat-steer]")).toHaveAttribute("data-chat-steer", "added");
    // The model ends its answer with no tool call, so the run never read it.
    await release(page);
    const answer = page.getByRole("paragraph").filter({ hasText: "Done. Each task now has a due date." });
    await expect(answer).toBeVisible();
    // The follow-up has no words of its own and draws no user bubble.
    const followUp = (await calls(page)).find((c) => c.kind === "followup");
    expect(followUp?.message).toBe("");
    await expect(page.locator("[data-chat-steer]")).toHaveCount(1);
    // The answer comes after the steer it answers.
    const steerBox = await page.locator("[data-chat-steer]").boundingBox();
    const answerBox = await answer.boundingBox();
    expect(answerBox!.y).toBeGreaterThan(steerBox!.y);
    expect((await calls(page)).map((c) => c.kind)).toEqual(["run", "steer", "followup"]);
  });

  test("a steer, then Stop: the run halts and no follow-up is asked", async ({ page }) => {
    // Review of #920, round 2: a Stop asks for no follow-up.
    const box = await startRun(page);
    await box.fill(STEER_TEXT);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.locator("[data-chat-steer]")).toHaveAttribute("data-chat-steer", "added");
    await page.getByRole("button", { name: "Stop the reply" }).click();
    await expect.poll(async () => (await calls(page)).map((c) => c.kind)).toEqual(["run", "steer", "cancel"]);
    await expect(page.locator("[data-chat-primary]")).toHaveAttribute("data-chat-primary", "send");
    // Give a follow-up time to fire, if one were owed.
    await page.waitForTimeout(2500);
    expect((await calls(page)).map((c) => c.kind)).toEqual(["run", "steer", "cancel"]);
  });

  test("Stop halts the run, and Esc from an empty box does the same", async ({ page }) => {
    const box = await startRun(page);
    await page.getByRole("button", { name: "Stop the reply" }).click();
    await expect.poll(async () => (await calls(page)).map((c) => c.kind)).toEqual(["run", "cancel"]);
    await expect(page.locator("[data-chat-primary]")).toHaveAttribute("data-chat-primary", "send");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
    // No follow-up after a Stop.
    expect((await calls(page)).map((c) => c.kind)).not.toContain("followup");

    // A second run, stopped with Esc from the empty box.
    await box.fill("Plan it again");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByRole("button", { name: "Stop the reply" })).toBeVisible();
    await box.focus();
    await page.keyboard.press("Escape");
    await expect.poll(async () => (await calls(page)).filter((c) => c.kind === "cancel").length).toBe(2);
  });
});
