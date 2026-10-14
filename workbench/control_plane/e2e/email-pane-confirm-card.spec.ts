import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * EM-G11c1b item 1 (`email_app_master_plan.md` §12.3.13.6b): the chat pane of
 * the Email app shows a confirmation card, and its answer goes back through
 * `POST /api/agent/respond-input`.
 *
 * The page is the real `/email` page, and the pane is the real
 * `EmailAssistantChat`, opened by its "Chat" button. No gateway runs. The
 * stream is a `fetch` override in the page, as in `email-deep-link.spec.ts`.
 *
 * The card events are the ones the gateway sends. A run of the REAL executor
 * (`run_agent_stream("email-assistant", shows_cards=True)`, the REAL agent and
 * its REAL `manage_inbox` and `draft_reply`) printed them on 2026-10-15. The
 * BFF route turns AG-UI `CUSTOM` into `{type: "custom", name, value}`
 * (`app/api/agent/chat/route.ts`). The stream holds after the card, as the
 * run waits, and goes on only when the page answers through respond-input.
 */

const ACCOUNT = {
  id: "acc-1",
  provider: "microsoft",
  email_address: "jane@acme.test",
  label: "Work",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
};

type Frame = Record<string, unknown>;

interface Turn {
  /** The tool the run calls, and its arguments. */
  tool: string;
  args: Record<string, unknown>;
  /** The card the tool asks, as the gateway sends it. */
  card: { title: string; detail: string; context: string };
  /** The result of the tool for each answer. */
  results: { APPROVE: string; REJECT: string };
}

const ARCHIVE: Turn = {
  tool: "manage_inbox",
  args: { action: "archive", message_ids: ["0b0b0b0b-0000-4000-8000-00000000000b"] },
  card: {
    title: "Archive 1 message in Work · jane@acme.test?",
    detail: "Mailbox: Work · jane@acme.test.",
    context: "Each message:\n- Kim: Offer 0",
  },
  results: {
    APPROVE: "archive: affected 1 message(s).",
    REJECT: "Cancelled — nothing changed in the mailbox.",
  },
};

const DRAFT: Turn = {
  tool: "draft_reply",
  args: { email_id: "m1", account_id: "acc-1", save: true },
  card: {
    title: "Save a reply draft in Work · jane@acme.test?",
    detail:
      "The assistant writes a reply to this email and saves it in the Drafts folder of " +
      "Work · jane@acme.test. Nothing is sent.",
    context: "The email:\n- from: kim@contoso.test\n- subject: Hi",
  },
  results: {
    APPROVE: "Draft from Work · jane@acme.test (mailbox acc-1) (saved to Drafts):\n\nHi Kim, thanks for the offer.",
    REJECT: "Cancelled — no draft was saved.",
  },
};

async function installMailbox(page: Page) {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 0, unread_count: 0 }]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [], total: 0, page: 1, page_size: 50 }),
  );
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

/** The run: a line of text, the card, then a wait for the answer. */
async function installRun(page: Page, turn: Turn) {
  await page.addInitScript((turn) => {
    window.localStorage.clear();
    const w = window as unknown as Record<string, unknown>;
    const chatBodies: unknown[] = [];
    const answers: unknown[] = [];
    w.__chatBodies = chatBodies;
    w.__answers = answers;
    const RID = "2083a6b56c5e411598023a40d2802b94";
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    let answer: (a: string) => void = () => {};
    const answered = new Promise<string>((r) => { answer = r; });
    let live: string | null = null;
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/agent/respond-input")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        answers.push(body);
        answer(String(body.answer));
        return json({ ok: true });
      }
      // The run is live on the server until the card is answered, so a pane
      // that opens again reconnects to it (`useAgentChat`).
      if (url.includes("/api/chat/active-sessions")) {
        return json(live ? [{ threadId: live, agentName: "email-assistant" }] : []);
      }
      if (url.includes("/api/chat/sessions")) {
        if (url.includes("/room")) return json({ detail: "no gateway in this suite" }, 503);
        return (init?.method ?? "GET").toUpperCase() === "GET" ? json([]) : json({ ok: true });
      }
      if (url.includes("/api/auth/me")) {
        return json({
          email: "dev@e2e.test", user_id: "dev", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat", "email"],
          features_denied: [], agents: ["email-assistant"], permissions: ["*"],
          capabilities: [], denied: [], is_admin: true,
        });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        // A reconnect (the pane opened again) replays the run from its
        // start, as the gateway's relay does, and then waits on with it.
        if (!body.reconnect) {
          chatBodies.push(body);
          live = String(body.threadId ?? "");
          void answered.then(() => { live = null; });
        }
        const stream = new ReadableStream<Uint8Array>({
          async start(c) {
            const send = (e: unknown) => {
              try { c.enqueue(frame(e)); } catch { /* the pane closed this stream */ }
            };
            send({ type: "message_start", messageId: "m-1" });
            send({ type: "delta", messageId: "m-1", content: "I need your approval first." });
            // As the BFF sends them (`route.ts`): the start carries no
            // arguments, and `tool_args` brings them once they are whole.
            send({ type: "tool_start", id: "t-1", name: turn.tool, args: {} });
            send({ type: "tool_args", id: "t-1", args: turn.args });
            send({ type: "custom", name: "confirmation_requested", value: { ...turn.card, request_id: RID } });
            // The run waits on the card, as `request_confirmation` does.
            const said = (await answered) === "APPROVE" ? "APPROVE" : "REJECT";
            send({ type: "custom", name: "confirmation_resolved", value: { request_id: RID, answer: said } });
            send({
              type: "tool_end", id: "t-1", name: turn.tool, success: true, args: turn.args,
              result: turn.results[said],
            });
            send({ type: "message_end", messageId: "m-1" });
            send({ type: "done" });
            try { c.close(); } catch { /* closed already */ }
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, turn);
}

const SHOTS = process.env.EMAIL_PANE_SHOTS;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** Opens the chat pane of the Email app and sends one turn. */
async function ask(page: Page, turn: Turn, words: string) {
  await page.setViewportSize({ width: 1280, height: 860 });
  await installMailbox(page);
  await installRun(page, turn);
  await page.goto("/email");
  const chatButton = page.getByRole("button", { name: "Chat", exact: true }).first();
  await expect(chatButton).toBeVisible({ timeout: 60_000 });
  await chatButton.click();
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill(words);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("I need your approval first.").first()).toBeVisible({ timeout: 15_000 });
}

const bodies = (page: Page) =>
  page.evaluate(() => (window as unknown as { __chatBodies: Record<string, unknown>[] }).__chatBodies.slice());
const answers = (page: Page) =>
  page.evaluate(() => (window as unknown as { __answers: Record<string, unknown>[] }).__answers.slice());

test.describe("the chat pane of the Email app shows the confirmation card", () => {
  test.describe.configure({ timeout: 120_000 });

  test("archive: one card in the pane, and Approve answers through respond-input", async ({ page }) => {
    await ask(page, ARCHIVE, "archive the newest email from Kim");
    // The pane, not the Chat app: the page stays on /email.
    expect(new URL(page.url()).pathname).toBe("/email");
    // The pane runs the email assistant on the executor path (`/agent/run/stream`).
    const [sent] = await bodies(page);
    expect(sent).toMatchObject({ agentName: "email-assistant", mode: "copilot" });

    const card = page.locator("[data-confirmation-card]");
    await expect(card).toHaveCount(1);
    await expect(card).toBeVisible();
    await expect(card).toContainText("Archive 1 message in Work · jane@acme.test?");
    // The card draws the context as fields: "Kim" beside "Offer 0".
    await expect(card).toContainText("Offer 0");
    await shot(page, "1-archive-card-in-pane");

    await card.getByRole("button", { name: "Approve" }).click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    const [a] = await answers(page);
    expect(a).toMatchObject({ request_id: "2083a6b56c5e411598023a40d2802b94", answer: "APPROVE" });
    await expect(card).toHaveCount(0);
    // The inbox-action card of `EmailToolCards` draws the result.
    await expect(page.getByText("Archived 1 email", { exact: true })).toBeVisible({ timeout: 15_000 });
    await shot(page, "2-archive-approved");
  });

  test("archive: Reject answers through respond-input, and the email stays", async ({ page }) => {
    await ask(page, ARCHIVE, "archive the newest email from Kim");
    const card = page.locator("[data-confirmation-card]");
    await expect(card).toHaveCount(1);
    await card.getByRole("button", { name: "Reject" }).click();
    await expect.poll(() => answers(page)).toHaveLength(1);
    expect((await answers(page))[0]).toMatchObject({ answer: "REJECT" });
    await expect(card).toHaveCount(0);
    await expect(page.getByText("Cancelled — nothing changed in the mailbox.").first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "3-archive-rejected");
  });

  test("close the pane while the card waits, open it again: one card, one answer", async ({ page }) => {
    await ask(page, ARCHIVE, "archive the newest email from Kim");
    const card = page.locator("[data-confirmation-card]");
    await expect(card).toHaveCount(1);
    // "Back to inbox" unmounts the pane. The run waits on, as the gateway's
    // detached run does.
    await page.getByRole("button", { name: "Back to inbox" }).click();
    await expect(card).toHaveCount(0);
    await page.getByRole("button", { name: "Chat", exact: true }).first().click();
    await expect(card).toHaveCount(1, { timeout: 15_000 });
    await shot(page, "6-card-after-reopen");
    await card.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Archived 1 email", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(card).toHaveCount(0);
    // The act happens once: one answer reached respond-input.
    expect(await answers(page)).toHaveLength(1);
  });

  test("draft: after Approve, the draft card says Saved to Drafts and offers no Save", async ({ page }) => {
    await ask(page, DRAFT, "draft a reply to this email");
    const card = page.locator("[data-confirmation-card]");
    await expect(card).toHaveCount(1);
    await expect(card).toContainText("Save a reply draft in Work · jane@acme.test?");
    await shot(page, "4-draft-card-in-pane");
    await card.getByRole("button", { name: "Approve" }).click();
    await expect.poll(() => answers(page)).toHaveLength(1);

    const draft = page.locator('[data-email-card="draft"]');
    await expect(draft).toBeVisible({ timeout: 15_000 });
    await expect(draft.getByText("Saved to Drafts")).toBeVisible();
    await expect(draft.getByRole("button", { name: "Save to Drafts" })).toHaveCount(0);
    await expect(draft.getByRole("button", { name: "Send", exact: true })).toHaveCount(0);
    await expect(draft.getByRole("textbox", { name: "Draft reply" })).toBeDisabled();
    await shot(page, "5-draft-saved");
  });
});
