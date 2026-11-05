import { expect, test, type Page } from "@playwright/test";

/**
 * A redraft through the chat keeps the member's edits (`email_app_master_plan.md`
 * §18.11, owner 2026-11-05). In a real browser: the member edits the one card
 * of a new mail, asks for a change in the chat, and reads "Draft 2" in place.
 *
 * The chat stream is a stub, as in `chat-steer.spec.ts`. The first request
 * starts the run, which shows the card and waits. The member's message is a
 * steer: it answers 202 `{steered: true}`, and the stub records its body. The
 * run then closes the old card as REVISED, and waits for the test to release
 * the new draft, so each moment can be read.
 */

const OLD = "a".repeat(32);
const NEW = "b".repeat(32);
const KIM = "kim@contoso.test";
const FIRST = "Hi Kim,\n\nPlease find the rates for May attached.";
const EDITED = "Hi Kim,\n\nPlease find the rates for May attached. Call me on Friday.";
const SHORTER = "Hi Kim,\n\nThe May rates are attached. Call me on Friday.";
const STEER = "make it shorter";

function form(body: string, link: Record<string, unknown> = {}) {
  const people = { to: [KIM], cc: [] };
  return {
    kind: "new", message_id: "", mailbox: "Work · jane@acme.test", subject: "Rates for May",
    original: { from: "", subject: "", received: "", excerpt: "" },
    recipients: { reply: people, reply_all: people }, reply_all: true, body, max_body: 20000,
    draft_id: "", note: "", notices: [], files: [], ...link,
  };
}

async function install(page: Page) {
  await page.addInitScript(({ old, fresh, first, shorter, steer, oldForm, newForm }) => {
    window.localStorage.clear();
    const w = window as unknown as Record<string, unknown>;
    const steers: unknown[] = [];
    w.__steers = steers;
    let released: () => void = () => {};
    const release = new Promise<void>((r) => { released = r; });
    w.__release = () => released();
    let steered: () => void = () => {};
    const steeredOnce = new Promise<void>((r) => { steered = r; });
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
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
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        if (body.message === steer) {
          steers.push(body);
          steered();
          return json({ steered: true, delivered: true, pendingReplay: false, reason: "folded_into_live_run" }, 202);
        }
        const stream = new ReadableStream<Uint8Array>({
          async start(c) {
            const send = (e: unknown) => {
              try { c.enqueue(frame(e)); } catch { /* closed */ }
            };
            send({ type: "message_start", messageId: "m-1" });
            send({ type: "delta", messageId: "m-1", content: "Here is the email to Kim.\n\n" });
            send({ type: "tool_start", id: "t-1", name: "send_email", args: {} });
            send({ type: "custom", name: "confirmation_requested", value: {
              title: "Email to kim@contoso.test", detail: "From Work · jane@acme.test · Rates for May",
              request_id: old, reply: oldForm,
            } });
            await steeredOnce;
            // The steer closes the card as REVISED (`close_on_steer`).
            send({ type: "custom", name: "confirmation_resolved", value: { request_id: old, answer: "REVISED" } });
            send({ type: "tool_end", id: "t-1", name: "send_email", success: true,
                   result: "The member wrote in the chat while the card was open." });
            await release;
            send({ type: "tool_start", id: "t-2", name: "send_email", args: {} });
            send({ type: "custom", name: "confirmation_requested", value: {
              title: "Email to kim@contoso.test", detail: "From Work · jane@acme.test · Rates for May",
              request_id: fresh, reply: newForm,
            } });
            // The run waits on the new card. The stream stays open.
            void first; void shorter;
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, {
    old: OLD, fresh: NEW, first: FIRST, shorter: SHORTER, steer: STEER,
    oldForm: form(FIRST), newForm: form(SHORTER, { revises: OLD, draft: 2 }),
  });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function openCard(page: Page) {
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
  await box.fill("Write to Kim about the May rates.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-new-mail-card]")).toBeVisible({ timeout: 15_000 });
  return box;
}

test.describe("a redraft through the chat keeps the member's edits", () => {
  test.describe.configure({ timeout: 120_000 });

  test("edit the card, ask in the chat, and read Draft 2 in place", async ({ page }) => {
    const box = await openCard(page);
    // While the card is open, the composer names the email a message changes.
    await expect(page.locator("[data-redraft-changing]")).toHaveText("Changing: Email to Kim");

    // The member edits the text, then asks for a change in the chat.
    await page.getByRole("textbox", { name: "Email text" }).fill(EDITED);
    await box.fill(STEER);
    // The composer's own Send: the card has a Send of its own.
    await page.locator('[data-chat-primary="send"]').click();

    // The steer carried the member's own version of the card.
    await expect.poll(() => page.evaluate(() => (window as unknown as { __steers: unknown[] }).__steers.length)).toBe(1);
    const [sent] = await page.evaluate(() => (window as unknown as { __steers: { cardDraft?: unknown }[] }).__steers);
    expect(sent.cardDraft).toEqual({
      request_id: OLD, subject: "Rates for May", body: EDITED, to: [KIM], cc: [], bcc: [],
    });

    // The closed card keeps its slot, with no buttons.
    const slot = page.locator('[data-redraft-slot="updating"]');
    await expect(slot).toHaveText("Updating the draft…");
    const card = page.locator("[data-confirmation-card]");
    await expect(card).toHaveCount(1);
    await expect(card.getByRole("button")).toHaveCount(0);

    // The new draft takes the same slot, as Draft 2, with a Changed: line
    // from the member's own version.
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    await expect(page.locator("[data-redraft-label]")).toHaveText("Draft 2");
    await expect(page.locator("[data-confirmation-card]")).toHaveCount(1);
    await expect(slot).toHaveCount(0);
    const changed = page.locator("[data-redraft-changed]");
    await expect(changed).toContainText("Changed:");
    await expect(changed.locator("del").first()).toBeVisible();
    await expect(changed.locator("mark").first()).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Email text" })).toHaveValue(SHORTER);
  });
});
