import { expect, test, type Page } from "@playwright/test";

/**
 * The owner's report of 2026-10-09: an orchestrator run drew a blocking
 * `optionPicker` ("How should we handle the forward to Geo?"), the owner
 * clicked an option, and "it doesn't seem to do anything". Production showed
 * that `respond-input` answered 200 and the run finished 6 s later, so the
 * fault is in what the chat DRAWS after the click.
 *
 * This spec replays that flow in a real browser, against the real chat page.
 * The stream is held open inside the page (a `fetch` override), so the card
 * waits exactly as a parked run does, and the run goes on only after the
 * click reaches `/api/agent/respond-input`:
 *
 *   1. an orchestrator turn writes a line, then parks on the card;
 *   2. the member clicks;
 *   3. `respond-input` answers 200 (or 409 for the fallback case);
 *   4. the run streams its follow-up text and finishes.
 *
 * What it found, before the fix: the click DID reach the run. But the other
 * options never dimmed (a `both` animation fill beat the inline opacity), no
 * word said the answer went, and the follow-up text drew ABOVE the card. So
 * the card looked untouched while the answer streamed out of view.
 *
 * No gateway runs under this suite. Every request the page makes either has a
 * stub here, or fails at once (`GATEWAY_RETRY_DEADLINE_MS=0`).
 */

const QUESTION = "How should we handle the forward to Geo?";
const FOLLOW_UP = "Done. I forwarded the BQ email with its PDF to Geo.";

const OPTIONS = [
  { id: "fwd", label: "Forward with the PDF", description: "Send the original email and its file.", recommended: true },
  { id: "link", label: "Send a link", description: "Write a new email with a link to the original." },
  { id: "skip", label: "Do nothing", description: "Leave it for now." },
];

type Scenario = {
  /** The card the run parks on: the generative-UI picker, or ask_questions. */
  ask: "picker" | "question";
  /** The status that `/api/agent/respond-input` answers. */
  respondStatus: number;
  /** A multi-select card instead of a single one. */
  multi?: boolean;
};

function askEvent(scenario: Scenario): Record<string, unknown> {
  if (scenario.ask === "question") {
    return {
      type: "custom",
      name: "elicitation_requested",
      value: {
        request_id: "req-forward-1",
        questions: [{
          header: "Forward",
          question: QUESTION,
          multiSelect: !!scenario.multi,
          allowFreeformInput: false,
          options: OPTIONS.map(({ label, description, recommended }) => ({ label, description, recommended })),
        }],
      },
    };
  }
  return {
    type: "custom",
    name: "generative_ui",
    value: {
      type: "template",
      props: {
        name: "optionPicker",
        data: {
          title: QUESTION,
          description: "Pick one. The run waits for you.",
          multi: !!scenario.multi,
          options: OPTIONS,
        },
      },
      request_id: "req-forward-1",
    },
  };
}

async function installScenario(page: Page, scenario: Scenario) {
  await page.addInitScript(
    ({ ask, followUp, respondStatus }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const respondBodies: unknown[] = [];
      const chatBodies: unknown[] = [];
      w.__respondBodies = respondBodies;
      w.__chatBodies = chatBodies;
      let release: () => void = () => {};
      const released = new Promise<void>((r) => { release = r; });
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status, headers: { "content-type": "application/json" },
        });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input
          : input instanceof URL ? input.href : input.url;
        if (url.includes("/api/agent/respond-input")) {
          respondBodies.push(JSON.parse(String(init?.body ?? "{}")));
          // The run resumes a beat AFTER the answer lands, as a real run
          // does, so the spec sees the card in the moment between.
          if (respondStatus === 200) setTimeout(release, 1500);
          return json(respondStatus === 200 ? { ok: true } : { detail: "No question waits" }, respondStatus);
        }
        if (url.includes("/api/chat/active-sessions")) return json([]);
        // One stable member. Without it the access read fails, the chat scope
        // flaps, and the page drops its session list in the middle of a turn.
        if (url.includes("/api/auth/me")) {
          return json({
            email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat", "email"],
            features_denied: [], agents: ["orchestrator"], permissions: ["*"],
            capabilities: [], denied: [], is_admin: true,
          });
        }
        // The shell's health probe: up, so no "updating" notice covers the chat.
        if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
        // The session store answers empty and takes every write. A slow or
        // failed history read can race a live turn, which is a separate
        // defect (recorded in the owning spec) and not the one replayed here.
        if (url.includes("/api/chat/sessions")) {
          const method = (init?.method ?? "GET").toUpperCase();
          if (url.includes("/room")) return json({ detail: "not a room" }, 404);
          return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
        }
        if (url.includes("/api/agent/chat")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          chatBodies.push(body);
          if (body.reconnect) return json({}, 404);
          const first = chatBodies.length === 1;
          const stream = new ReadableStream<Uint8Array>({
            async start(controller) {
              if (!first) {
                // The fallback path: the answer went out as a NEW message.
                controller.enqueue(frame({ type: "message_start", messageId: "m-9" }));
                controller.enqueue(frame({ type: "delta", content: `Got it: ${body.message}`, messageId: "m-9" }));
                controller.enqueue(frame({ type: "done" }));
                controller.close();
                return;
              }
              controller.enqueue(frame({ type: "message_start", messageId: "m-1" }));
              controller.enqueue(frame({ type: "delta", content: "The email has a PDF, so I have three ways to send it.", messageId: "m-1" }));
              controller.enqueue(frame({ type: "message_end", messageId: "m-1" }));
              controller.enqueue(frame({ type: "tool_start", id: "t-ui", name: "emit_generative_ui", args: {} }));
              controller.enqueue(frame(ask));
              if (respondStatus !== 200) {
                // A stale card: the stream was cut (a deploy, a lost worker)
                // with no `done`, so the card stays and nothing waits on it.
                controller.close();
                return;
              }
              // Parked: nothing more until the member answers.
              await released;
              controller.enqueue(frame({ type: "tool_end", id: "t-ui", name: "emit_generative_ui", success: true, result: "{\"ok\":true}" }));
              controller.enqueue(frame({ type: "message_start", messageId: "m-2" }));
              controller.enqueue(frame({ type: "delta", content: followUp, messageId: "m-2" }));
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
    { ask: askEvent(scenario), followUp: FOLLOW_UP, respondStatus: scenario.respondStatus },
  );

  // The shell's reads. Each answers empty, so the page renders with no gateway.
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function startTurn(page: Page) {
  await page.goto("/chat");
  // A fresh visit opens the agent picker. The default agent is the
  // orchestrator, which drew the owner's card.
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Forward the BQ email with its PDF to Geo");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(QUESTION).first()).toBeVisible({ timeout: 15_000 });
}

const respondBodies = (page: Page) =>
  page.evaluate(() => (window as unknown as { __respondBodies: Array<Record<string, unknown>> }).__respondBodies);
const chatBodies = (page: Page) =>
  page.evaluate(() => (window as unknown as { __chatBodies: Array<Record<string, unknown>> }).__chatBodies);

/** The follow-up draws BELOW the card the member just answered. */
async function expectFollowUpBelowCard(page: Page) {
  const follow = page.getByRole("paragraph").filter({ hasText: FOLLOW_UP });
  await expect(follow).toBeVisible({ timeout: 10_000 });
  const cardBox = await page.getByText(QUESTION).first().boundingBox();
  const followBox = await follow.boundingBox();
  expect(cardBox, "the card is on the page").not.toBeNull();
  expect(followBox!.y, "the follow-up draws below the card").toBeGreaterThan(cardBox!.y);
}

test.describe("A blocking option picker answers the click", () => {
  // The first visit compiles the page on a cold `next dev`, which can pass
  // the suite's 45 s default by itself (CI starts cold).
  test.describe.configure({ timeout: 120_000 });

  test("single choice: the option shows as chosen, the card locks, and the follow-up draws below it", async ({ page }) => {
    await installScenario(page, { ask: "picker", respondStatus: 200 });
    await startTurn(page);

    const option = page.getByRole("button", { name: /Forward with the PDF/ });
    await option.click();

    // At once, before the run goes on: chosen, locked, and said so.
    await expect(option).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Sent", { exact: true })).toBeVisible();
    const other = page.getByRole("button", { name: /Send a link/ });
    await expect(other).toBeDisabled();
    // The lock SHOWS: the other options dim. A `both` animation fill used to
    // hold them at full opacity, so nothing on screen changed.
    await expect.poll(() => other.evaluate((el) => getComputedStyle(el).opacity)).toBe("0.5");

    // The click reached the parked run, with the run's own request id.
    await expect.poll(async () => (await respondBodies(page)).at(-1)?.answer)
      .toBe("Selected: Forward with the PDF");
    expect((await respondBodies(page)).at(-1)?.request_id).toBe("req-forward-1");

    await expectFollowUpBelowCard(page);
    // Still chosen once the run has finished: the card did not remount.
    await expect(option).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  });

  test("the recommended option reads Recommended", async ({ page }) => {
    await installScenario(page, { ask: "picker", respondStatus: 200 });
    await startTurn(page);
    const option = page.getByRole("button", { name: /Forward with the PDF/ });
    await expect(option.getByText("Recommended", { exact: true })).toBeVisible();
  });

  test("multi choice: Confirm sends the picks, then the card locks", async ({ page }) => {
    await installScenario(page, { ask: "picker", respondStatus: 200, multi: true });
    await startTurn(page);

    await page.getByRole("button", { name: /Forward with the PDF/ }).click();
    await page.getByRole("button", { name: /Send a link/ }).click();
    await page.getByRole("button", { name: "Confirm selection" }).click();

    await expect(page.getByText("Sent", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /Do nothing/ })).toBeDisabled();
    await expect.poll(async () => (await respondBodies(page)).at(-1)?.answer)
      .toBe("Selected: Forward with the PDF, Send a link");
    await expectFollowUpBelowCard(page);
  });

  test("a stale card (409) sends the answer as a message, so it is never lost", async ({ page }) => {
    await installScenario(page, { ask: "picker", respondStatus: 409 });
    await startTurn(page);

    await page.getByRole("button", { name: /Send a link/ }).click();
    await expect.poll(async () => (await chatBodies(page)).length).toBe(2);
    expect((await chatBodies(page)).at(-1)?.message).toBe("Selected: Send a link");
    await expect(page.getByText("Got it: Selected: Send a link").first()).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("The ask_questions card answers the click", () => {
  // The first visit compiles the page on a cold `next dev`, which can pass
  // the suite's 45 s default by itself (CI starts cold).
  test.describe.configure({ timeout: 120_000 });

  test("submit with 200 resumes the run, and the recommended option reads Recommended", async ({ page }) => {
    await installScenario(page, { ask: "question", respondStatus: 200 });
    await startTurn(page);

    const option = page.getByRole("button", { name: /Forward with the PDF/ });
    await expect(option.getByText("Recommended", { exact: true })).toBeVisible();
    await option.click();
    await expect(option).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Submit", exact: true }).click();

    await expect.poll(async () => (await respondBodies(page)).at(-1)?.answer)
      .toBe("Forward with the PDF");
    await expect(page.getByRole("paragraph").filter({ hasText: FOLLOW_UP })).toBeVisible({ timeout: 10_000 });
  });

  test("submit with 409 sends the answer as a message, so it is never lost", async ({ page }) => {
    await installScenario(page, { ask: "question", respondStatus: 409 });
    await startTurn(page);

    await page.getByRole("button", { name: /Send a link/ }).click();
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect.poll(async () => (await chatBodies(page)).length).toBe(2);
    expect((await chatBodies(page)).at(-1)?.message).toBe("Send a link");
    await expect(page.getByText("Got it: Send a link").first()).toBeVisible({ timeout: 10_000 });
  });
});
