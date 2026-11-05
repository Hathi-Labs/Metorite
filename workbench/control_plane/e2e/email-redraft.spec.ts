import { expect, test, type Page } from "@playwright/test";

/**
 * A redraft through the chat keeps the member's edits (`email_app_master_plan.md`
 * §18.11, owner 2026-11-05). In a real browser: the member edits the one card
 * of a new mail, asks for a change in the chat, and reads "Draft 2" in place.
 *
 * The chat stream is a stub, as in `chat-steer.spec.ts`. The first request
 * starts the run, which shows the cards and waits. The member's message is a
 * steer: it answers 202 `{steered: true}`, and the stub records its body. The
 * run then closes the old cards as REVISED, and waits for the test to release
 * the new drafts, so each moment can be read.
 *
 * Fix round 2 (rule 7): the GATEWAY starts the person the member unticked
 * unticked, and names that person (`ticked`, `unticked`). The stub's new forms
 * carry what the gateway sends, and the test reads that the card draws them.
 */

const OLD = "a".repeat(32);
const NEW = "b".repeat(32);
const OLD_BOB = "c".repeat(32);
const NEW_BOB = "d".repeat(32);
const KIM = "kim@contoso.test";
const GEO = "geo@acme.test";
const BOB = "bob@contoso.test";
const FIRST = "Hi Kim,\n\nPlease find the rates for May attached.";
const EDITED = "Hi Kim,\n\nPlease find the rates for May attached. Call me on Friday.";
const SHORTER = "Hi Kim,\n\nThe May rates are attached. Call me on Friday.";
const STEER = "make it shorter";

function form(body: string, link: Record<string, unknown> = {}, people = { to: [KIM], cc: [GEO] }) {
  return {
    kind: "new", message_id: "", mailbox: "Work · jane@acme.test", subject: "Rates for May",
    original: { from: "", subject: "", received: "", excerpt: "" },
    recipients: { reply: people, reply_all: people }, reply_all: true, body, max_body: 20000,
    draft_id: "", note: "", notices: [], files: [], ...link,
  };
}

/** What the gateway adds to the next card: Geo starts unticked, and is named. */
const KEPT = { ticked: { reply: { to: [KIM], cc: [] }, reply_all: { to: [KIM], cc: [] } }, unticked: [GEO] };

interface StubCard { request_id: string; title: string; reply: unknown }

const stubCard = (id: string, title: string, reply: unknown): StubCard => ({ request_id: id, title, reply });

async function install(page: Page, firstCards: StubCard[], newCards: StubCard[]) {
  await page.addInitScript(({ steer, firstCards, newCards }) => {
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
            send({ type: "delta", messageId: "m-1", content: "Here is the email.\n\n" });
            firstCards.forEach((c, i) => {
              send({ type: "tool_start", id: `t-${i}`, name: "send_email", args: {} });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: c.title, detail: "From Work · jane@acme.test · Rates for May",
                request_id: c.request_id, reply: c.reply,
              } });
            });
            await steeredOnce;
            // The steer names no card, so it closes each one as REVISED (`close_on_steer`).
            firstCards.forEach((c, i) => {
              send({ type: "custom", name: "confirmation_resolved", value: { request_id: c.request_id, answer: "REVISED" } });
              send({ type: "tool_end", id: `t-${i}`, name: "send_email", success: true,
                     result: "The member wrote in the chat while the card was open." });
            });
            await release;
            newCards.forEach((c, i) => {
              send({ type: "tool_start", id: `n-${i}`, name: "send_email", args: {} });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: c.title, detail: "From Work · jane@acme.test · Rates for May",
                request_id: c.request_id, reply: c.reply,
              } });
            });
            // The run waits on the new cards. The stream stays open.
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { steer: STEER, firstCards, newCards });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function openCard(page: Page, firstCards: StubCard[], newCards: StubCard[]) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page, firstCards, newCards);
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
    const box = await openCard(page,
      [stubCard(OLD, `Email to ${KIM}`, form(FIRST))],
      [stubCard(NEW, `Email to ${KIM}`, form(SHORTER, { revises: OLD, draft: 2, ...KEPT }))]);
    // While the card is open, the composer names the email a message changes.
    await expect(page.locator("[data-redraft-changing]")).toHaveText("Changing: Email to Kim");

    // The member edits the text, then asks for a change in the chat.
    await page.getByRole("textbox", { name: "Email text" }).fill(EDITED);
    // P1: the member takes the always-copy colleague out.
    await page.getByRole("checkbox", { name: `Send to ${GEO}` }).uncheck();
    await box.fill(STEER);
    // The composer's own Send: the card has a Send of its own.
    await page.locator('[data-chat-primary="send"]').click();

    // The steer carried the member's own version of the card.
    await expect.poll(() => page.evaluate(() => (window as unknown as { __steers: unknown[] }).__steers.length)).toBe(1);
    const [sent] = await page.evaluate(() => (window as unknown as { __steers: { cardDraft?: unknown }[] }).__steers);
    expect(sent.cardDraft).toEqual([{
      request_id: OLD, subject: "Rates for May", body: EDITED, to: [KIM], cc: [], bcc: [],
    }]);

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
    // P1: the new card offers the colleague again, and the colleague stays
    // unticked. One click would otherwise send to a person the member removed.
    await expect(page.getByRole("checkbox", { name: `Send to ${GEO}` })).not.toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Send to ${KIM}` })).toBeChecked();
    await expect(page.locator("[data-redraft-kept-out]")).toHaveText(`Still unticked from your last draft: ${GEO}.`);
    // The line reads cleanly: no semicolon, and a word on each side of a comma.
    await expect(changed).not.toContainText(";");
  });

  test("one message closes two cards, and the colleague stays unticked on the new card", async ({ page }) => {
    // Path 1 of the review: the gateway links neither new card ("many"), so no
    // card takes a slot by its link. The gateway still starts Geo unticked.
    const bobPeople = { to: [BOB], cc: [] as string[] };
    const box = await openCard(page,
      [stubCard(OLD, `Email to ${KIM}`, form(FIRST)), stubCard(OLD_BOB, `Email to ${BOB}`, form(FIRST, {}, bobPeople))],
      [stubCard(NEW_BOB, `Email to ${BOB}`, form(SHORTER, {}, bobPeople)), stubCard(NEW, `Email to ${KIM}`, form(SHORTER, KEPT))]);
    // The member unticks Geo on Kim's card, then asks for a change.
    await page.getByRole("tab", { name: /Kim/ }).click();
    await page.getByRole("checkbox", { name: `Send to ${GEO}` }).uncheck();
    await box.fill(STEER);
    await page.locator('[data-chat-primary="send"]').click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __steers: unknown[] }).__steers.length)).toBe(1);
    const [sent] = await page.evaluate(() => (window as unknown as { __steers: { cardDraft?: { request_id: string; cc: string[] }[] }[] }).__steers);
    expect(sent.cardDraft?.map((d) => d.request_id).sort()).toEqual([OLD, OLD_BOB].sort());
    expect(sent.cardDraft?.find((d) => d.request_id === OLD)?.cc).toEqual([]);
    // Both slots wait: the row says so, and never "0 to approve".
    await expect(page.locator("[data-card-tabs-count]")).toHaveText("Updating 2 drafts");
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    // Each new card took a slot. No slot is left beside the drafts that came.
    await expect(page.locator("[data-card-tabs-count]")).toHaveText("2 to approve");
    await expect(page.locator("[data-redraft-slot]")).toHaveCount(0);
    await page.getByRole("tab", { name: /Kim/ }).click();
    await expect(page.getByRole("textbox", { name: "Email text" })).toHaveValue(SHORTER);
    await expect(page.getByRole("checkbox", { name: `Send to ${GEO}` })).not.toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Send to ${KIM}` })).toBeChecked();
    await expect(page.locator("[data-redraft-kept-out]")).toHaveText(`Still unticked from your last draft: ${GEO}.`);
  });
});
