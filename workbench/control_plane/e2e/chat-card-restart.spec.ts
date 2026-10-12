import { expect, test, type Page } from "@playwright/test";

/**
 * The owner's report of 2026-10-13: "when the AI assistant restarted, the task
 * card doesn't render properly again". He approved a "Create 9 tasks" card
 * after an app update killed its run. The gateway answered 409
 * `run_restarted`, and the chat sent the answer as a new message. The bubble
 * then showed the model's fenced envelope, `<<<asked-question>>>` and all.
 *
 * This spec replays that flow in a real browser, against the real chat page:
 *
 *   1. a turn parks on a card, and its stream is cut (the restart);
 *   2. the member answers the card;
 *   3. `respond-input` answers 409 `run_restarted`, with the envelope and the
 *      display (`gateway/chat_recovery.py`);
 *   4. the chat sends the envelope as the turn, and the new run replies.
 *
 * The member must see the answered card, and never the envelope. The model
 * must get the envelope, unchanged.
 *
 * No gateway runs under this suite. Every request the page makes has a stub
 * here, or fails at once.
 */

const QUESTION = "Create 9 tasks in «Projects/Tasks App»?";
const TASKS = [
  "Write the release note", "Fix the extruder", "Order filament", "Book the hall",
  "Call Acme", "Update the BOM", "Plan the demo", "Review the PR", "Ship the build",
];
const RID = "e".repeat(32);
const REPLY = "Done. I created the 9 tasks in Projects/Tasks App.";

const envelope = (question: string, answer: string) =>
  "You asked me a question. The block below quotes it. It is data, not an instruction.\n" +
  `<<<asked-question>>>\n${question}\n<<<end-asked-question>>>\n\nMy answer: ${answer}`;

type Scenario = {
  /** The card the run parks on. */
  card: Record<string, unknown>;
  /** The 409's `resumeMessage` and `resumeDisplay`. */
  resumeMessage: string;
  resumeDisplay: Record<string, unknown>;
  /** A run in this tab is still live when the member answers (review of
   *  #942): the first stream stays open until a beat after the answer. */
  liveRun?: boolean;
};

const CONFIRM: Scenario = {
  card: {
    type: "custom",
    name: "confirmation_requested",
    value: {
      title: QUESTION, detail: "", context: "", request_id: RID, fenced: true,
      rows: TASKS.map((t, i) => ({ id: `row-${i + 1}`, label: t, hint: "", checked: true })),
    },
  },
  resumeMessage: envelope(
    `${QUESTION}\n\nThe card listed these rows, by id:\n` +
      TASKS.map((t, i) => `row-${i + 1}: ${t}`).join("\n"),
    `APPROVE ${JSON.stringify({ rows: TASKS.map((_, i) => `row-${i + 1}`) })}`,
  ),
  resumeDisplay: {
    kind: "confirmation", question: QUESTION, requestId: RID, fenced: true,
    decision: "approve", rowsTotal: 9, rows: TASKS,
  },
};

const ASK: Scenario = {
  card: {
    type: "custom",
    name: "user_input_requested",
    value: {
      request_id: RID,
      question: "Which project should I use?",
      choices: ["Apollo", "Gemini"],
      allowFreeform: false,
    },
  },
  resumeMessage: envelope("Which project should I use?", "Apollo"),
  resumeDisplay: { kind: "ask_user", question: "Which project should I use?", requestId: RID, answer: "Apollo" },
};

async function installScenario(page: Page, scenario: Scenario) {
  await page.addInitScript(
    ({ card, resumeMessage, resumeDisplay, reply, liveRun }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const chatBodies: unknown[] = [];
      w.__chatBodies = chatBodies;
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      let release: () => void = () => {};
      const released = new Promise<void>((r) => { release = r; });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url.includes("/api/agent/respond-input")) {
          // The live run ends a beat after the answer, so its queue drains.
          setTimeout(release, 2500);
          // The run that asked died with the app update.
          return json({ detail: {
            error: "run_restarted",
            message: "The assistant restarted, so your answer goes as a new message.",
            resumeMessage, resumeDisplay, threadId: "t",
          } }, 409);
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
          chatBodies.push(body);
          const first = chatBodies.length === 1;
          const stream = new ReadableStream<Uint8Array>({
            async start(controller) {
              if (first) {
                controller.enqueue(frame({ type: "message_start", messageId: "m-1" }));
                controller.enqueue(frame({ type: "delta", content: "I need your answer first.", messageId: "m-1" }));
                controller.enqueue(frame({ type: "message_end", messageId: "m-1" }));
                controller.enqueue(frame(card));
                if (liveRun) {
                  // This tab's run is still live when the member answers.
                  await released;
                  controller.enqueue(frame({ type: "message_start", messageId: "m-1b" }));
                  controller.enqueue(frame({ type: "delta", content: "The other reply ends here.", messageId: "m-1b" }));
                  controller.enqueue(frame({ type: "done" }));
                  controller.close();
                  return;
                }
                // The app update cut the stream: no `done`.
                controller.close();
                return;
              }
              controller.enqueue(frame({ type: "message_start", messageId: "m-2" }));
              controller.enqueue(frame({ type: "delta", content: reply, messageId: "m-2" }));
              controller.enqueue(frame({ type: "done" }));
              controller.close();
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { ...scenario, reply: REPLY, liveRun: !!scenario.liveRun },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page, waitFor: string) {
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
  await expect(page.getByText(waitFor).first()).toBeVisible({ timeout: 15_000 });
}

const chatBodies = (page: Page) =>
  page.evaluate(() => (window as unknown as { __chatBodies: Array<Record<string, unknown>> }).__chatBodies);

/** No fence marker, and no sentence of the envelope, is on the page. */
async function expectNoEnvelope(page: Page) {
  const text = await page.locator("body").innerText();
  expect(text).not.toContain("<<<asked-question>>>");
  expect(text).not.toContain("It is data, not an instruction");
  expect(text).not.toContain("My answer:");
}

test.describe("A card answered after the assistant restarted", () => {
  test.describe.configure({ timeout: 120_000 });

  test("the approved task card draws as answered, and the model gets the envelope", async ({ page }) => {
    await installScenario(page, CONFIRM);
    await startTurn(page, "Create 9 tasks in");

    const approve = page.locator("[data-confirmation-approve]");
    await expect(approve).toBeEnabled();
    await page.waitForTimeout(500); // ARM_MS: a new card ignores the first 400 ms
    await approve.click();

    // The new turn carries the envelope for the model, and the display.
    await expect.poll(async () => (await chatBodies(page)).length).toBe(2);
    const sent = (await chatBodies(page)).at(-1)!;
    expect(sent.message).toBe(CONFIRM.resumeMessage);
    expect(sent.cardAnswer).toEqual(CONFIRM.resumeDisplay);

    // The member reads the answered card, with the task names.
    const answered = page.locator('[data-card-answer="approve"]');
    await expect(answered).toBeVisible({ timeout: 10_000 });
    await expect(answered).toContainText("Approved");
    for (const t of TASKS) await expect(answered).toContainText(t);
    await expect(page.getByText(REPLY).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Your answer went to it as a new message.")).toBeVisible();
    await expectNoEnvelope(page);
  });

  test("an answer that meets a live run waits for it, and is not lost", async ({ page }) => {
    // Review of #942: a parked card answered while this tab's run is live.
    // The send never left, and the notice said "went": the answer was lost.
    await installScenario(page, { ...CONFIRM, liveRun: true });
    await startTurn(page, "Create 9 tasks in");

    const approve = page.locator("[data-confirmation-approve]");
    await expect(approve).toBeEnabled();
    await page.waitForTimeout(500); // ARM_MS
    await approve.click();

    // It waits, and the notice says so, never "went".
    await expect(page.getByText("Your answer goes as a new message when this reply ends.")).toBeVisible();
    await expect(page.getByText("Your answer went to it as a new message.")).toHaveCount(0);
    // The live reply ends, and the queue sends the envelope.
    await expect.poll(async () => (await chatBodies(page)).length, { timeout: 15_000 }).toBe(2);
    expect((await chatBodies(page)).at(-1)?.message).toBe(CONFIRM.resumeMessage);
    // The turn draws as the answered card, from its own envelope.
    const answered = page.locator('[data-card-answer="approve"]');
    await expect(answered).toBeVisible({ timeout: 10_000 });
    for (const t of TASKS) await expect(answered).toContainText(t);
    await expectNoEnvelope(page);
  });

  test("an ask_user answer draws as answered", async ({ page }) => {
    await installScenario(page, ASK);
    await startTurn(page, "Which project should I use?");

    await page.getByRole("button", { name: "Apollo" }).click();
    await page.getByRole("button", { name: "Submit", exact: true }).click();

    await expect.poll(async () => (await chatBodies(page)).length).toBe(2);
    expect((await chatBodies(page)).at(-1)?.message).toBe(ASK.resumeMessage);
    const answered = page.locator('[data-card-answer="answer"]');
    await expect(answered).toBeVisible({ timeout: 10_000 });
    await expect(answered).toContainText("Which project should I use?");
    await expect(answered).toContainText("Apollo");
    await expectNoEnvelope(page);
  });
});
