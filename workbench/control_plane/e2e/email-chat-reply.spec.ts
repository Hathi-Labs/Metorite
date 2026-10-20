import { expect, test, type Page } from "@playwright/test";

/**
 * EM-CR (`email_app_master_plan.md` §18): one email reply, done in the MAIN
 * chat. Owner request of 2026-10-20: the assistant describes the email, the
 * reply card shows, the member chooses Reply or Reply all, edits the text and
 * presses Send, and the reply completes with no Email app opened.
 *
 * The page is the real `/chat` with the orchestrator. No gateway runs. The
 * stream is a `fetch` override in the page, as in `email-chat-cards.spec.ts`.
 * The email assistant runs as a delegated sub-agent, so its card arrives on
 * the parent's stream as `confirmation_requested` with a `reply` form, the
 * shape `request_confirmation(..., reply=form)` emits. The stream waits on
 * the card, as the run does, and goes on only when the page answers through
 * `POST /api/agent/respond-input`.
 *
 * What this holds: the description shows before the card; the toggle swaps
 * the server's recipient lists and the page derives none; the answer carries
 * the edited body and the shown lists; the run goes on and its result shows;
 * the page stays on `/chat` and calls no `/api/email/*` route.
 */

const RID = "5b1d0c2e8f6a4e0f9a3b7c1d2e4f6a8b";
const DESCRIPTION =
  "Kim Lee wrote about the quote for June. She asks you to confirm the price by Friday.";

const REPLY_FORM = {
  mailbox: "Work · jane@acme.test",
  subject: "Re: Quote for June",
  original: {
    from: "Kim Lee <kim@contoso.test>",
    subject: "Quote for June",
    received: "2026-10-19T09:30:00Z",
    excerpt: "Hi Jane, can you confirm the June price by Friday? Lee and Ops are copied.",
  },
  recipients: {
    reply: { to: ["kim@contoso.test"], cc: [] },
    reply_all: { to: ["kim@contoso.test", "lee@contoso.test"], cc: ["ops@contoso.test"] },
  },
  reply_all: false,
  body: "Hi Kim,\n\nThe June price stands. I confirm it.\n\nJane",
  note: "",
};

const RESULTS: Record<string, string> = {
  send: "Sent to kim@contoso.test, lee@contoso.test, with a copy to ops@contoso.test, from Work · jane@acme.test.",
  save: "Saved in Drafts of Work · jane@acme.test, to kim@contoso.test. Nothing was sent.",
  REJECT: "Reply discarded and cancelled. Nothing was sent or saved.",
};

async function install(page: Page) {
  await page.addInitScript(
    ({ rid, form, description, results }) => {
      window.localStorage.clear();
      const w = window as unknown as Record<string, unknown>;
      const answers: unknown[] = [];
      const emailCalls: string[] = [];
      w.__answers = answers;
      w.__emailCalls = emailCalls;
      const enc = new TextEncoder();
      const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      let answer: (a: string) => void = () => {};
      const answered = new Promise<string>((r) => { answer = r; });
      const orig = window.fetch.bind(window);
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const method = (init?.method ?? "GET").toUpperCase();
        const path = new URL(url, window.location.href).pathname;
        if (path.startsWith("/api/email/")) {
          emailCalls.push(`${method} ${path}`);
          return json({});
        }
        if (url.includes("/api/agent/respond-input")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          answers.push(body);
          answer(String(body.answer));
          return json({ ok: true });
        }
        if (url.includes("/api/chat/active-sessions")) return json([]);
        if (url.includes("/api/auth/me")) {
          return json({
            email: "jane@acme.test", user_id: "jane", authenticated: true, is_active: true,
            organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
            roles: ["owner"], legacy_role: "executive", features: ["chat", "email"],
            features_denied: [], agents: ["orchestrator", "email-assistant"], permissions: ["*"],
            capabilities: [], denied: [], is_admin: true,
          });
        }
        if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
        if (url.includes("/api/chat/sessions")) {
          if (url.includes("/room")) return json({ detail: "not a room" }, 404);
          return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
        }
        if (url.includes("/api/agent/chat")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          if (body.reconnect) return json({}, 404);
          const stream = new ReadableStream<Uint8Array>({
            async start(c) {
              const send = (e: unknown) => {
                try { c.enqueue(frame(e)); } catch { /* closed */ }
              };
              send({ type: "message_start", messageId: "m-1" });
              send({ type: "delta", messageId: "m-1", content: `${description}\n\n` });
              send({ type: "tool_start", id: "t-1", name: "email_assistant", args: {} });
              send({ type: "tool_args", id: "t-1", args: { task: "Reply to the mail from Kim" } });
              send({
                type: "custom",
                name: "confirmation_requested",
                value: {
                  title: "Reply to Kim Lee <kim@contoso.test>?",
                  detail: "From Work · jane@acme.test · Re: Quote for June",
                  context: "",
                  request_id: rid,
                  reply: form,
                },
              });
              const said = await answered;
              let key = "REJECT";
              if (said.startsWith("APPROVE ")) key = JSON.parse(said.slice(8)).reply.action;
              send({
                type: "custom",
                name: "confirmation_resolved",
                value: { request_id: rid, answer: key === "REJECT" ? "REJECT" : "APPROVE" },
              });
              send({ type: "tool_end", id: "t-1", name: "email_assistant", success: true, result: results[key] });
              send({ type: "delta", messageId: "m-1", content: results[key] });
              send({ type: "message_end", messageId: "m-1" });
              send({ type: "done" });
              try { c.close(); } catch { /* closed */ }
            },
          });
          return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        return orig(input, init);
      };
    },
    { rid: RID, form: REPLY_FORM, description: DESCRIPTION, results: RESULTS },
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function ask(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("What did Kim send me? Reply to her.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(DESCRIPTION).first()).toBeVisible({ timeout: 15_000 });
}

const answers = (page: Page) =>
  page.evaluate(() => (window as unknown as { __answers: Record<string, unknown>[] }).__answers.slice());
const emailCalls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __emailCalls: string[] }).__emailCalls.slice());

function parsed(answer: unknown): Record<string, unknown> {
  const text = String(answer);
  expect(text.startsWith("APPROVE ")).toBe(true);
  return JSON.parse(text.slice("APPROVE ".length)).reply;
}

test.describe("the reply card in the main chat", () => {
  test.describe.configure({ timeout: 120_000 });

  test("describe, card, Reply all, edit, Send: the run goes on and says Sent", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-reply-card]");
    await expect(card).toBeVisible();
    // The card says what it answers, in the sender's words.
    await expect(card).toContainText("Kim Lee <kim@contoso.test>");
    await expect(card).toContainText("Quote for June");
    await expect(card.locator("[data-reply-excerpt]")).toContainText("confirm the June price");
    // Reply: the sender only.
    const to = card.locator('[data-reply-recipients="to"]');
    await expect(to).toHaveText(/kim@contoso\.test$/);
    await expect(card.locator('[data-reply-recipients="cc"]')).toHaveCount(0);
    // Reply all: the server's second list, in full.
    await card.locator('[data-reply-mode="reply-all"]').click();
    await expect(to).toContainText("kim@contoso.test, lee@contoso.test");
    await expect(card.locator('[data-reply-recipients="cc"]')).toContainText("ops@contoso.test");
    const box = card.getByRole("textbox", { name: "Reply text" });
    await expect(box).toHaveValue(REPLY_FORM.body);
    await box.fill("Hi all,\n\nConfirmed for June.\n\nJane");
    await card.locator("[data-reply-send]").click();

    await expect.poll(() => answers(page)).toHaveLength(1);
    const [a] = await answers(page);
    expect(a).toMatchObject({ request_id: RID });
    expect(parsed(a.answer)).toEqual({
      action: "send",
      reply_all: true,
      body: "Hi all,\n\nConfirmed for June.\n\nJane",
      to: ["kim@contoso.test", "lee@contoso.test"],
      cc: ["ops@contoso.test"],
    });
    await expect(card).toHaveCount(0);
    await expect(page.getByText(RESULTS.send).first()).toBeVisible({ timeout: 15_000 });
    // No Email app opened, and the page reached no email route itself.
    expect(new URL(page.url()).pathname).toBe("/chat");
    expect(await emailCalls(page)).toEqual([]);
  });

  test("Save to Drafts answers save, with the Reply list", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-reply-card]");
    await card.locator("[data-reply-save]").click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    expect(parsed((await answers(page))[0].answer)).toEqual({
      action: "save",
      reply_all: false,
      body: REPLY_FORM.body,
      to: ["kim@contoso.test"],
      cc: [],
    });
    await expect(page.getByText(RESULTS.save).first()).toBeVisible({ timeout: 15_000 });
    expect(await emailCalls(page)).toEqual([]);
  });

  test("Discard answers REJECT, and nothing is sent or saved", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-reply-card]");
    await card.locator("[data-reply-discard]").click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    expect((await answers(page))[0]).toMatchObject({ request_id: RID, answer: "REJECT" });
    await expect(card).toHaveCount(0);
    await expect(page.getByText(RESULTS.REJECT).first()).toBeVisible({ timeout: 15_000 });
    expect(await emailCalls(page)).toEqual([]);
  });

  test("an empty body cannot be sent", async ({ page }) => {
    await ask(page);
    const card = page.locator("[data-reply-card]");
    await card.getByRole("textbox", { name: "Reply text" }).fill("   ");
    await expect(card.locator("[data-reply-send]")).toBeDisabled();
    await expect(card.locator("[data-reply-save]")).toBeDisabled();
    expect(await answers(page)).toEqual([]);
  });
});
