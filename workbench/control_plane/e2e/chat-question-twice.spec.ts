import { expect, test, type Page } from "@playwright/test";

/**
 * Two questions in a row that share a header (review of PR #945, P1).
 *
 * An answered question card stays in view until its run ends. The next
 * question takes the same place. With no `key`, React kept the first card's
 * picks and typed text, and the answer reads them by header. So the second
 * answer carried the first one's pick, Submit was on before any choice, and
 * an `ask_user` box (header "Question", always) still held the old text.
 *
 * The stream is held inside the page: each card waits until its answer
 * reaches `/api/agent/respond-input`, as a parked run does.
 */

type Ask = { kind: "questions" | "user"; rid: string; options: string[]; freeform: boolean };

function askFrame(a: Ask): Record<string, unknown> {
  if (a.kind === "user") {
    return {
      type: "custom",
      name: "user_input_requested",
      value: { request_id: a.rid, question: `Which place, ${a.rid}?`, choices: a.options, allowFreeform: a.freeform },
    };
  }
  return {
    type: "custom",
    name: "elicitation_requested",
    value: {
      request_id: a.rid,
      questions: [{
        header: "Scope",
        question: `Which tasks, ${a.rid}?`,
        allowFreeformInput: a.freeform,
        options: a.options.map((label) => ({ label })),
      }],
    },
  };
}

async function install(page: Page, asks: Ask[]) {
  await page.addInitScript(({ asks, frames }) => {
    window.localStorage.clear();
    const w = window as unknown as Record<string, unknown>;
    const bodies: Record<string, unknown>[] = [];
    w.__respond = bodies;
    const gates: (() => void)[] = [];
    const waits = asks.map(() => new Promise<void>((r) => gates.push(r)));
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/agent/respond-input")) {
        bodies.push(JSON.parse(String(init?.body ?? "{}")));
        setTimeout(() => gates[bodies.length - 1]?.(), 50);
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
        ((w.__chat ??= []) as unknown[]).push(body);
        // A steer is accepted and starts no second stream.
        if (((w.__chat as unknown[]).length) > 1) return json({ ok: true });
        const stream = new ReadableStream<Uint8Array>({
          async start(c) {
            c.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            c.enqueue(frame({ type: "delta", content: "Two things first.", messageId: "m-1" }));
            for (let i = 0; i < frames.length; i++) {
              c.enqueue(frame(frames[i]));
              await waits[i];
            }
            // Held open after the last answer, so nothing clears the cards.
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { asks, frames: asks.map(askFrame) });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page) {
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Tidy the assistant tasks");
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

const respond = (page: Page) =>
  page.evaluate(() => (window as unknown as { __respond: Record<string, unknown>[] }).__respond);

test.describe("a second question starts empty", () => {
  test.describe.configure({ timeout: 120_000 });

  test("ask_questions: the second answer holds only the second pick", async ({ page }) => {
    await install(page, [
      { kind: "questions", rid: "a".repeat(32), options: ["#22", "#23"], freeform: false },
      { kind: "questions", rid: "b".repeat(32), options: ["#24", "#25"], freeform: false },
    ]);
    await startTurn(page);

    const first = page.locator('[data-elicitation-card="open"]');
    await expect(first).toContainText(`Which tasks, ${"a".repeat(32)}?`, { timeout: 15_000 });
    await first.getByRole("button", { name: "#22" }).click();
    await first.getByRole("button", { name: "Submit", exact: true }).click();
    await expect.poll(async () => (await respond(page)).length).toBe(1);

    const second = page.locator('[data-elicitation-card="open"]');
    await expect(second).toContainText(`Which tasks, ${"b".repeat(32)}?`);
    const submit = second.getByRole("button", { name: "Submit", exact: true });
    await expect(submit).toBeDisabled();
    await expect(second.locator('[aria-pressed="true"]')).toHaveCount(0);
    await second.getByRole("button", { name: "#25" }).click();
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect.poll(async () => (await respond(page)).length).toBe(2);
    const bodies = await respond(page);
    expect(bodies[0]).toMatchObject({ request_id: "a".repeat(32), answer: "#22" });
    expect(bodies[1]).toMatchObject({ request_id: "b".repeat(32), answer: "#25", was_freeform: false });
  });

  test("ask_user: the second box starts empty, and the old text never goes", async ({ page }) => {
    await install(page, [
      { kind: "user", rid: "c".repeat(32), options: [], freeform: true },
      { kind: "user", rid: "d".repeat(32), options: ["staging", "production"], freeform: true },
    ]);
    await startTurn(page);

    const first = page.locator('[data-elicitation-card="open"]');
    await expect(first).toContainText(`Which place, ${"c".repeat(32)}?`, { timeout: 15_000 });
    await first.getByRole("textbox", { name: "Your answer" }).fill("staging-2");
    await first.getByRole("button", { name: "Submit", exact: true }).click();
    await expect.poll(async () => (await respond(page)).length).toBe(1);

    const second = page.locator('[data-elicitation-card="open"]');
    await expect(second).toContainText(`Which place, ${"d".repeat(32)}?`);
    await expect(second.getByRole("textbox", { name: "Your own answer" })).toHaveValue("");
    const submit = second.getByRole("button", { name: "Submit", exact: true });
    await expect(submit).toBeDisabled();
    await second.getByRole("button", { name: "production" }).click();
    await submit.click();

    await expect.poll(async () => (await respond(page)).length).toBe(2);
    const bodies = await respond(page);
    expect(bodies[0]).toMatchObject({ request_id: "c".repeat(32), answer: "staging-2", was_freeform: true });
    expect(bodies[1]).toMatchObject({ request_id: "d".repeat(32), answer: "production", was_freeform: false });
  });
});

// Owner report, 2026-10-23 (`lib/typedAnswer.ts`, spec §24.10 rule C6). The
// owner typed the answer to a question in the message box. It went as a
// steer, and the run waited on the question for minutes.
test.describe("a typed message answers the one waiting question", () => {
  test.describe.configure({ timeout: 120_000 });

  test("ask_user: the box says so, the text answers the card, and nothing steers", async ({ page }) => {
    await install(page, [{ kind: "user", rid: "e".repeat(32), options: [], freeform: true }]);
    await startTurn(page);

    const card = page.locator('[data-elicitation-card="open"]');
    await expect(card).toContainText(`Which place, ${"e".repeat(32)}?`, { timeout: 15_000 });
    const box = page.getByRole("textbox", { name: /^Message / });
    await box.fill("Veena is the name");
    await expect(page.locator("[data-steer-caption]")).toHaveText("Answers the question above");
    await page.getByRole("button", { name: "Send", exact: true }).click();

    await expect.poll(async () => (await respond(page)).length).toBe(1);
    expect((await respond(page))[0]).toMatchObject({
      request_id: "e".repeat(32), answer: "Veena is the name", was_freeform: true,
    });
    await expect(box).toHaveValue("");
    const chats = await page.evaluate(() => ((window as unknown as { __chat: unknown[] }).__chat ?? []).length);
    expect(chats).toBe(1);
  });

  test("ask_questions of one pick-only question: a typed message steers", async ({ page }) => {
    await install(page, [{ kind: "questions", rid: "f".repeat(32), options: ["#22", "#23"], freeform: false }]);
    await startTurn(page);

    await expect(page.locator('[data-elicitation-card="open"]')).toBeVisible({ timeout: 15_000 });
    await page.getByRole("textbox", { name: /^Message / }).fill("also tidy the old ones");
    await expect(page.locator("[data-steer-caption]")).toHaveText("Adds to the current reply");
  });
});
