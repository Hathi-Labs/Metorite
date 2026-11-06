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
 *
 * Fix round 3: a new mail to another person, in the same message, takes no
 * link and no slot. It shows as its own card, and Kim's draft keeps Kim's slot.
 *
 * Fix round 4: a slot ends with a Stop and with a failed model call, a card to
 * other people leaves the slot its end line, the view goes to a live card, the
 * chat claims nothing at rest, and a change of tab keeps the member's edits.
 */

const OLD = "a".repeat(32);
const NEW = "b".repeat(32);
const OLD_BOB = "c".repeat(32);
const NEW_BOB = "d".repeat(32);
const KIM = "kim@contoso.test";
const GEO = "geo@acme.test";
const BOB = "bob@contoso.test";
const CAROL = "carol@contoso.test";
const CAROL_ID = "e".repeat(32);
const LATE = "Hi Carol,\n\nI am running ten minutes late.";
const MIXED = "first email Carol that I am late, then make this shorter";
const ALSO = "also email Carol that I am late";
const ONLY_KIM = "make the one to kim shorter";
const BOB_TEXT = "Hi Bob,\n\nThe spare parts ship on Monday.";
const NOTE = `Unticked earlier in this chat: ${GEO}. Tick the box to include them.`;
const NOT_CHANGED = "The draft did not change.";
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

interface StubOptions {
  /** The message that steers the run. */
  steer?: string;
  /** The cards that the steer closes, by id. With none, each first card closes. */
  closes?: string[];
  /** The model call fails once the steer closed the cards. */
  failAfterSteer?: boolean;
  /** The first stream is cut, and the run goes on, and ends, on the attach stream. */
  cutFirstStream?: boolean;
}

const stubCard = (id: string, title: string, reply: unknown): StubCard => ({ request_id: id, title, reply });

async function install(page: Page, firstCards: StubCard[], newCards: StubCard[], options: StubOptions = {}) {
  await page.addInitScript(({ steer, firstCards, newCards, closes, failAfterSteer, cutFirstStream }) => {
    window.localStorage.clear();
    const w = window as unknown as Record<string, unknown>;
    const steers: unknown[] = [];
    w.__steers = steers;
    let released: () => void = () => {};
    const release = new Promise<void>((r) => { released = r; });
    w.__release = () => released();
    let finished: () => void = () => {};
    const finish = new Promise<void>((r) => { finished = r; });
    w.__finish = () => finished();
    const answers: unknown[] = [];
    let liveThread: string | null = null;
    w.__attached = 0;
    w.__answers = answers;
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
      if (url.includes("/api/agent/cancel")) return json({ cancelled: true });
      if (url.includes("/api/agent/respond-input")) {
        answers.push(JSON.parse(String(init?.body ?? "{}")));
        return json({ ok: true });
      }
      if (url.includes("/api/chat/active-sessions")) {
        return json(liveThread ? [{ threadId: liveThread, agentName: "orchestrator", state: "running", live: true, runId: "r-1" }] : []);
      }
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
        if (body.reconnect) {
          if (!cutFirstStream) return json({}, 404);
          w.__attached = Number(w.__attached) + 1;
          // The attach stream: it replays the run, and the run ends on it.
          const attach = new ReadableStream<Uint8Array>({
            async start(ctl) {
              const send = (e: unknown) => {
                try { ctl.enqueue(frame(e)); } catch { /* closed */ }
              };
              send({ type: "message_start", messageId: "m-1" });
              firstCards.forEach((c, i) => {
                send({ type: "tool_start", id: `t-${i}`, name: "send_email", args: {} });
                send({ type: "custom", name: "confirmation_requested", value: {
                  title: c.title, detail: "From Work · jane@acme.test · Rates for May",
                  request_id: c.request_id, reply: c.reply,
                } });
              });
              await steeredOnce;
              firstCards.forEach((c, i) => {
                send({ type: "custom", name: "confirmation_resolved", value: { request_id: c.request_id, answer: "REVISED" } });
                send({ type: "tool_end", id: `t-${i}`, name: "send_email", success: true,
                       result: "The member wrote in the chat while the card was open." });
              });
              await finish;
              liveThread = null;
              send({ type: "done", run_id: "r-1" });
              try { ctl.close(); } catch { /* closed */ }
            },
          });
          return new Response(attach, { status: 200, headers: { "content-type": "text/event-stream" } });
        }
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        if (body.message === steer) {
          steers.push(body);
          steered();
          return json({ steered: true, delivered: true, pendingReplay: false, reason: "folded_into_live_run" }, 202);
        }
        const stream = new ReadableStream<Uint8Array>({
          async start(ctl) {
            const send = (e: unknown) => {
              try { ctl.enqueue(frame(e)); } catch { /* closed */ }
            };
            const end = () => {
              try { ctl.close(); } catch { /* closed */ }
            };
            // A real fetch rejects its read on an abort, as a Stop does.
            init?.signal?.addEventListener("abort", () => {
              try { ctl.error(new DOMException("Aborted", "AbortError")); } catch { /* closed */ }
            });
            send({ type: "message_start", messageId: "m-1" });
            send({ type: "delta", messageId: "m-1", content: "Here is the email.\n\n" });
            firstCards.forEach((c, i) => {
              send({ type: "tool_start", id: `t-${i}`, name: "send_email", args: {} });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: c.title, detail: "From Work · jane@acme.test · Rates for May",
                request_id: c.request_id, reply: c.reply,
              } });
            });
            if (cutFirstStream) {
              // A cut with no `done`: the run goes on, and the server lists it.
              liveThread = String(body.threadId);
              end();
              return;
            }
            await steeredOnce;
            // The steer names no card, so it closes each one as REVISED (`close_on_steer`).
            firstCards.forEach((c, i) => {
              if (closes && !closes.includes(c.request_id)) return;
              send({ type: "custom", name: "confirmation_resolved", value: { request_id: c.request_id, answer: "REVISED" } });
              send({ type: "tool_end", id: `t-${i}`, name: "send_email", success: true,
                     result: "The member wrote in the chat while the card was open." });
            });
            if (failAfterSteer) {
              send({ type: "error", content: "The model call failed." });
              end();
              return;
            }
            await release;
            newCards.forEach((c, i) => {
              send({ type: "tool_start", id: `n-${i}`, name: "send_email", args: {} });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: c.title, detail: "From Work · jane@acme.test · Rates for May",
                request_id: c.request_id, reply: c.reply,
              } });
            });
            // The run waits on the new cards. The stream stays open.
            await finish;
            send({ type: "done", run_id: "r-1" });
            end();
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, {
    steer: options.steer ?? STEER, firstCards, newCards,
    closes: options.closes ?? null, failAfterSteer: !!options.failAfterSteer,
    cutFirstStream: !!options.cutFirstStream,
  });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function openCard(page: Page, firstCards: StubCard[], newCards: StubCard[], options: StubOptions = {}) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page, firstCards, newCards, options);
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
    await expect(page.locator("[data-redraft-kept-out]")).toHaveText(NOTE);
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
    await expect(page.locator("[data-redraft-kept-out]")).toHaveText(NOTE);
  });

  test("a new email to Carol in the same message shows as its own card, and Kim's Draft 2 keeps its slot", async ({ page }) => {
    // P1 of the review of fix round 2. The member unticks Geo, then asks for
    // a new email to Carol AND a change of Kim's card. The model draws
    // Carol's card first. The gateway gives it no link (its To is not Kim's),
    // and starts Geo unticked on it too: the always-copy rule names Geo.
    const carolPeople = { to: [CAROL], cc: [GEO] };
    const carolKept = { ticked: { reply: { to: [CAROL], cc: [] }, reply_all: { to: [CAROL], cc: [] } }, unticked: [GEO] };
    const box = await openCard(page,
      [stubCard(OLD, `Email to ${KIM}`, form(FIRST))],
      [stubCard(CAROL_ID, `Email to ${CAROL}`, { ...form(LATE, carolKept, carolPeople), subject: "Running late" }),
       stubCard(NEW, `Email to ${KIM}`, form(SHORTER, { revises: OLD, draft: 2, ...KEPT }))],
      { steer: MIXED });
    await page.getByRole("textbox", { name: "Email text" }).fill(EDITED);
    await page.getByRole("checkbox", { name: `Send to ${GEO}` }).uncheck();
    await box.fill(MIXED);
    await page.locator('[data-chat-primary="send"]').click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __steers: unknown[] }).__steers.length)).toBe(1);
    await expect(page.locator('[data-redraft-slot="updating"]')).toHaveText("Updating the draft…");
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    // Two cards wait. No slot is left: Kim's draft took Kim's slot.
    await expect(page.locator("[data-card-tabs-count]")).toHaveText("2 to approve");
    await expect(page.locator("[data-redraft-slot]")).toHaveCount(0);
    // Kim's tab: Draft 2, the Changed: line, and Geo still unticked.
    await page.getByRole("tab", { name: /Kim/ }).click();
    await expect(page.locator("[data-redraft-label]")).toHaveText("Draft 2");
    await expect(page.locator("[data-redraft-changed]")).toContainText("Changed:");
    await expect(page.getByRole("textbox", { name: "Email text" })).toHaveValue(SHORTER);
    await expect(page.getByRole("checkbox", { name: `Send to ${GEO}` })).not.toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Send to ${KIM}` })).toBeChecked();
    // Carol's tab: its own card, with no label and no Changed: line.
    await page.getByRole("tab", { name: /Carol/ }).click();
    await expect(page.getByRole("textbox", { name: "Email text" })).toHaveValue(LATE);
    await expect(page.locator("[data-redraft-label]")).toHaveCount(0);
    await expect(page.locator("[data-redraft-changed]")).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: `Send to ${CAROL}` })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Send to ${GEO}` })).not.toBeChecked();
    await expect(page.locator("[data-redraft-kept-out]")).toHaveText(NOTE);
  });

  // Fix round 4.
  const bobCard = () => stubCard(OLD_BOB, `Email to ${BOB}`, form(BOB_TEXT, {}, { to: [BOB], cc: [] }));
  const carolPeople = { to: [CAROL], cc: [GEO] };
  const carolKept = { ticked: { reply: { to: [CAROL], cc: [] }, reply_all: { to: [CAROL], cc: [] } }, unticked: [GEO] };
  const carolCard = () => stubCard(CAROL_ID, `Email to ${CAROL}`, { ...form(LATE, carolKept, carolPeople), subject: "Running late" });
  const steered = (page: Page) => expect.poll(() => page.evaluate(() => (window as unknown as { __steers: unknown[] }).__steers.length)).toBe(1);

  test("a Stop ends the slot, a blocking card stays, and the chat then claims nothing about the edits", async ({ page }) => {
    // P1 and P2 of the review of fix round 3. Only Kim's card closes.
    const box = await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST)), bobCard()], [], { steer: ONLY_KIM, closes: [OLD] });
    await expect(page.locator("[data-redraft-changing]")).toHaveText("Changing: 2 emails");
    await box.fill(ONLY_KIM);
    await page.locator('[data-chat-primary="send"]').click();
    await steered(page);
    const slot = page.locator("[data-redraft-slot]");
    await expect(slot).toHaveText("Updating the draft…");
    await expect(page.locator("[data-redraft-changing]")).toHaveText("Changing: Email to Bob");
    await page.locator('[data-chat-primary="stop"]').click();
    // The slot ends. It reads its end line, and then it closes.
    await expect(page.locator('[data-redraft-slot="unchanged"]')).toHaveText(NOT_CHANGED);
    await expect(slot).toHaveCount(0, { timeout: 8000 });
    // Bob's card waits still. A plain send carries no version, so the composer
    // names no email, and the nudge says what a request now does.
    const text = page.getByRole("textbox", { name: "Email text" });
    await expect(text).toHaveValue(BOB_TEXT);
    await expect(page.locator("[data-redraft-changing]")).toHaveCount(0);
    await text.fill("Hi Bob,\n\nMy own words.");
    await expect(page.locator('[data-new-mail-nudge="edited-at-rest"]')).toContainText("starts again from the assistant's draft");
    await expect(page.locator("[data-nudge-chip]")).toHaveCount(0);
  });

  test("a failed model call ends the slot", async ({ page }) => {
    const box = await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST))], [], { failAfterSteer: true });
    await box.fill(STEER);
    await page.locator('[data-chat-primary="send"]').click();
    await steered(page);
    await expect(page.locator('[data-redraft-slot="unchanged"]')).toHaveText(NOT_CHANGED);
    await expect(page.locator("[data-redraft-slot]")).toHaveCount(0, { timeout: 8000 });
  });

  test("a run that ends on the attach stream ends the slot", async ({ page }) => {
    // The stream was cut, and the chat attached to the live run. A `done` on
    // the attach stream emits no `onRunFinalized`, so the run's rest must end it.
    const box = await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST))], [], { cutFirstStream: true });
    await expect.poll(() => page.evaluate(() => Number((window as unknown as { __attached: number }).__attached)), { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
    await box.fill(STEER);
    await page.locator('[data-chat-primary="send"]').click();
    await steered(page);
    await expect(page.locator("[data-redraft-slot]")).toHaveText("Updating the draft…");
    await page.evaluate(() => (window as unknown as { __finish: () => void }).__finish());
    await expect(page.locator('[data-redraft-slot="unchanged"]')).toHaveText(NOT_CHANGED);
    await expect(page.locator("[data-redraft-slot]")).toHaveCount(0, { timeout: 8000 });
  });

  test("a new email to Carol alone: the view goes to her card, and Kim's slot says that it did not change", async ({ page }) => {
    // P1 and P2 of the review of fix round 3. The model draws only Carol's card.
    const box = await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST))], [carolCard()], { steer: ALSO });
    await box.fill(ALSO);
    await page.locator('[data-chat-primary="send"]').click();
    await steered(page);
    await expect(page.locator("[data-redraft-slot]")).toHaveText("Updating the draft…");
    await page.evaluate(() => (window as unknown as { __release: () => void }).__release());
    // The run waits on the member at Carol's card, so the view shows it.
    await expect(page.getByRole("textbox", { name: "Email text" })).toHaveValue(LATE);
    await expect(page.locator("[data-card-tabs-count]")).toHaveText("1 to approve");
    await expect(page.locator("[data-redraft-changing]")).toHaveText("Changing: Email to Carol");
    // The member can go back to the slot, and it stays in view.
    await page.getByRole("tab", { name: /Kim/ }).click();
    await expect(page.locator("[data-redraft-slot]")).toHaveText("Updating the draft…");
    // The run ends with no new draft of Kim's card, and the member reads so.
    await page.evaluate(() => (window as unknown as { __finish: () => void }).__finish());
    await expect(page.locator('[data-redraft-slot="unchanged"]')).toHaveText(NOT_CHANGED);
    await expect(page.locator("[data-redraft-slot]")).toHaveCount(0, { timeout: 8000 });
  });

  test("a change of tab keeps an untick and the text, and Send does not go to that person", async ({ page }) => {
    // P2 of the review of fix round 3, and a path to mail.
    await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST)), bobCard()], []);
    await page.getByRole("tab", { name: /Kim/ }).click();
    const text = page.getByRole("textbox", { name: "Email text" });
    await text.fill(EDITED);
    await page.getByRole("checkbox", { name: `Send to ${GEO}` }).uncheck();
    await page.getByRole("tab", { name: /Bob/ }).click();
    await expect(text).toHaveValue(BOB_TEXT);
    await page.getByRole("tab", { name: /Kim/ }).click();
    await expect(text).toHaveValue(EDITED);
    await expect(page.getByRole("checkbox", { name: `Send to ${GEO}` })).not.toBeChecked();
    await expect(page.getByRole("checkbox", { name: `Send to ${KIM}` })).toBeChecked();
    // The card arms again after a change of tab (`ARM_MS`).
    await page.waitForTimeout(600);
    await page.locator("[data-reply-send]").click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __answers: unknown[] }).__answers.length)).toBe(1);
    const [answer] = await page.evaluate(() => (window as unknown as { __answers: unknown[] }).__answers);
    expect(JSON.stringify(answer)).not.toContain(GEO);
    expect(JSON.stringify(answer)).toContain("Call me on Friday");
  });

  test("a change of tab keeps the version that a steer carries", async ({ page }) => {
    // The form used to report its start on the remount, and that deleted the
    // kept version. So the next steer carried Geo as ticked.
    const box = await openCard(page, [stubCard(OLD, `Email to ${KIM}`, form(FIRST)), bobCard()], []);
    await page.getByRole("tab", { name: /Kim/ }).click();
    await page.getByRole("textbox", { name: "Email text" }).fill(EDITED);
    await page.getByRole("checkbox", { name: `Send to ${GEO}` }).uncheck();
    await page.getByRole("tab", { name: /Bob/ }).click();
    await page.getByRole("tab", { name: /Kim/ }).click();
    await box.fill(STEER);
    await page.locator('[data-chat-primary="send"]').click();
    await steered(page);
    const [sent] = await page.evaluate(() => (window as unknown as { __steers: { cardDraft?: { request_id: string; cc: string[]; body: string }[] }[] }).__steers);
    const kim = sent.cardDraft?.find((d) => d.request_id === OLD);
    expect(kim?.cc).toEqual([]);
    expect(kim?.body).toBe(EDITED);
  });
});
