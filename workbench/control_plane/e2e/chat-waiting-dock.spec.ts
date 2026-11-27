import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * The waiting dock in a real browser (WD-1, `projects_ai_chat.md` §24.11).
 *
 * The dock sits between the thread and the composer form, never inside it.
 * Inside the form, a button of a card is a submit button: Enter in a card
 * field sent the message box, and a tap on an option sent the box text as
 * the answer (verifier P0, 2026-11-27). These tests fail when the dock moves
 * back into the form, when the fold row answers a card, or when the keyboard
 * rules K1, K2, K3 and K6 break.
 *
 * No gateway runs. The page's `fetch` is stubbed: the shell read says
 * `waiting_dock: true`, and the chat stream holds a new-mail card, a task card
 * with its edit form and an `ask_questions` card, and stays live.
 * `window.__answers` records each respond-input and `window.__sends` each chat
 * message. `window.__kb(px)` raises a fake soft keyboard of `px` pixels.
 */

const PID = "11111111-1111-1111-1111-111111111111";
const MAIL = "a".repeat(32);
const TASK = "c".repeat(32);
const ASK = "q".repeat(32);
const PEOPLE = { to: ["kim@contoso.test"], cc: [] };
const FORM = {
  kind: "new", message_id: "", mailbox: "Work · jane@acme.test", subject: "Rates for May",
  body: "Hi Kim,\n\nThe rates are attached.", recipients: { reply: PEOPLE, reply_all: PEOPLE },
  files: [], notices: [], note: "",
};
// A long email to five people: the card is far taller than the dock's cap.
const LONG_FORM = {
  ...FORM,
  recipients: {
    reply: { to: ["kim@contoso.test", "Ravi Kumar <ravi@contoso.test>", "lena@contoso.test"], cc: ["geo@acme.test", "ops@acme.test"] },
    reply_all: { to: ["kim@contoso.test", "Ravi Kumar <ravi@contoso.test>", "lena@contoso.test"], cc: ["geo@acme.test", "ops@acme.test"] },
  },
  body: ["Hi Kim,", "", ...Array.from({ length: 30 }, (_, i) => `Line ${i + 1} of the May rates, with the detail of each change.`), "", "Best,", "Jane"].join("\n"),
};

const VALUES = {
  due: "2026-12-01", start: "", status: "Doing", important: null, leveraged: false,
  description: "Ship the pack.", assignees: ["ann@acme.test"],
};

declare global {
  interface Window {
    __answers: { request_id: string; answer: string }[];
    __sends: string[];
    __release: () => void;
    __releaseQ: () => void;
    __cancels: number;
    __kb: (px: number) => void;
    __push: (e: unknown) => void;
  }
}

type Cards = "all" | "one" | "earlier";

// The plain card of `edit_draft` that names a draft of the chat (task 3978).
const EARLIER = "e".repeat(32);

// The reply card of `reply_card.py` (kind reply), as the gateway sends it.
const REPLY = "r".repeat(32);
const REPLY_CARD = {
  title: "Reply to Kim <kim@contoso.test>?", detail: "From Work · jane@acme.test · Re: Quote", request_id: REPLY,
  reply: {
    message_id: "0b0b0b0b-0000-4000-8000-00000000000b", notices: [], mailbox: "Work · jane@acme.test", subject: "Re: Quote",
    original: { from: "Kim <kim@contoso.test>", subject: "Quote", received: "2026-10-19T09:00:00Z", excerpt: "Can you confirm?" },
    recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["kim@contoso.test", "lee@contoso.test"], cc: [] } },
    reply_all: true, body: "Hi Kim,\n\nYes.", note: "",
  },
};

async function install(page: Page, opts: { cards: Cards; gated?: boolean; long?: boolean; lateQuestion?: boolean; withReply?: boolean }) {
  await page.addInitScript(({ cards, gated, form, values, pid, mail, task, ask, lateQuestion, replyCard, earlier }) => {
    window.localStorage.clear();
    window.localStorage.setItem("cc-shell-nav", "1");
    const w = window as unknown as Window;
    w.__answers = [];
    w.__sends = [];
    // A fake soft keyboard: the visual viewport loses `kb` px.
    let kb = 0;
    const vv = new EventTarget() as EventTarget & Record<string, unknown>;
    Object.defineProperty(vv, "height", { get: () => window.innerHeight - kb });
    Object.defineProperty(vv, "width", { get: () => window.innerWidth });
    Object.defineProperty(vv, "offsetTop", { get: () => 0 });
    Object.defineProperty(vv, "offsetLeft", { get: () => 0 });
    Object.defineProperty(vv, "scale", { get: () => 1 });
    Object.defineProperty(window, "visualViewport", { get: () => vv });
    w.__kb = (px: number) => {
      kb = px;
      vv.dispatchEvent(new Event("resize"));
      window.dispatchEvent(new Event("resize"));
    };
    let release: () => void = () => {};
    const released = new Promise<void>((r) => { release = r; });
    w.__release = () => release();
    let releaseQ: () => void = () => {};
    const releasedQ = new Promise<void>((r) => { releaseQ = r; });
    w.__releaseQ = () => releaseQ();
    w.__cancels = 0;
    let live: ReadableStreamDefaultController<Uint8Array> | null = null;
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const send = (e: unknown) => { try { live?.enqueue(frame(e)); } catch { /* closed */ } };
    w.__push = send;
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/auth/me/shell")) {
        return json({ answered: "answered", preset: null, pins: null, known: null, waiting_dock: true });
      }
      if (url.includes("/api/agent/respond-input")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        w.__answers.push(body);
        // The server closes the card, as the gateway does.
        send({ type: "custom", name: "confirmation_resolved", value: { request_id: body.request_id, answer: "APPROVE" } });
        return json({ ok: true });
      }
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (url.includes("/api/auth/me")) {
        return json({
          email: "jane@acme.test", user_id: "jane", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat", "email", "projects"],
          features_denied: [], agents: ["orchestrator"], permissions: ["*"],
          capabilities: [], denied: [], is_admin: true,
        });
      }
      if (url.includes("/api/health")) return json({ gateway: "up", build: "e2e" });
      if (url.includes("/api/chat/sessions")) {
        if (url.includes("/room")) return json({ detail: "not a room" }, 404);
        return method === "GET" ? json([]) : json({ ok: true, saved: 1, unchanged: [] });
      }
      if (url.includes("/api/agent/cancel")) {
        w.__cancels += 1;
        return json({ cancelled: true });
      }
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        w.__sends.push(String(body.message ?? ""));
        if (w.__sends.length > 1) return json({ steered: true, delivered: true, pendingReplay: false }, 202);
        const stream = new ReadableStream<Uint8Array>({
          async start(ctl) {
            live = ctl;
            send({ type: "message_start", messageId: "m-1" });
            send({ type: "delta", messageId: "m-1", content: "Here is what I need from you." });
            if (gated) await released;
            if (cards === "earlier") {
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Save these changes to the draft?", detail: "To kim@contoso.test · Rates for May",
                earlier: "To kim@contoso.test: Rates for May", request_id: earlier,
              } });
              return;
            }
            if (cards === "one") {
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Create this task?", detail: "«Ship the pack» · Launch", request_id: task,
              } });
              return;
            }
            send({ type: "custom", name: "confirmation_requested", value: {
              title: "Send this new email?", detail: "From Work · Rates for May", request_id: mail, reply: form,
            } });
            send({ type: "custom", name: "confirmation_requested", value: {
              title: "Create this task?", detail: "«Ship the pack» · in Launch · status Doing",
              context: "Check each field, then approve.\nproject: «Launch»\nname: «Ship the pack»\ndue_at: 2026-12-01",
              fenced: true, request_id: task, edit: { kind: "create_task", project_id: pid, values },
            } });
            if (replyCard) send({ type: "custom", name: "confirmation_requested", value: replyCard });
            // D4: a question that comes after the dock opened.
            if (lateQuestion) await releasedQ;
            send({ type: "custom", name: "elicitation_requested", value: {
              request_id: ask,
              questions: [{
                header: "Depot", question: "Which depot should the June run ship from?", multiSelect: false,
                allowFreeformInput: true, options: [{ label: "North depot" }, { label: "South depot" }],
              }],
            } });
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, {
    cards: opts.cards, gated: !!opts.gated, form: opts.long ? LONG_FORM : FORM, values: VALUES, pid: PID, mail: MAIL, task: TASK, ask: ASK,
    lateQuestion: !!opts.lateQuestion, replyCard: opts.withReply ? REPLY_CARD : null, earlier: EARLIER,
  });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/projects/assignees**", (r) => r.fulfill({ json: { people: [], agents: [], hr_visible: false } }));
  await page.route("**/api/projects/nodes/*/statuses**", (r) => r.fulfill({ json: {
    rows: ["Todo", "Doing", "Done"].map((name, position) => ({ id: `lane-${position}`, project_id: PID, name, color: "", position, category: "active" })),
    total: 3, counts: {}, owner_id: PID,
  } }));
  await page.route("**/api/projects/people/names**", (r) => r.fulfill({ json: { names: {} } }));
}

const answers = (page: Page) => page.evaluate(() => window.__answers);
const sends = (page: Page) => page.evaluate(() => window.__sends);
const box = (page: Page) => page.getByRole("textbox", { name: /^Message / });
const dock = (page: Page) => page.locator("[data-waiting-dock]");
/** A control of the OPEN card only: every card of the dock stays mounted. */
const open = (page: Page, sel: string) => page.locator(`[data-dock-body] [role="tabpanel"] ${sel}`);
/** The Reject of the open card. */
const reject = (page: Page) => page.locator('[data-dock-body] [role="tabpanel"]').getByRole("button", { name: "Reject" });
/** A programmatic click on each control of the open card, at once. */
const clickNow = (page: Page, sel: string) =>
  page.evaluate((s) => document.querySelectorAll<HTMLElement>(`[data-dock-body] [role="tabpanel"] ${s}`).forEach((b) => b.click()), sel);

async function start(page: Page, opts: { cards: Cards; gated?: boolean; long?: boolean; lateQuestion?: boolean; withReply?: boolean }) {
  await install(page, opts);
  await page.goto("/chat");
  const picker = page.getByRole("button", { name: /General-purpose/i }).first();
  await expect(picker.or(box(page)).first()).toBeVisible({ timeout: 90_000 });
  if (await picker.isVisible().catch(() => false)) await picker.click();
  await expect(box(page)).toBeEnabled({ timeout: 30_000 });
  await box(page).fill("Plan the June run.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Here is what I need from you.").first()).toBeVisible({ timeout: 20_000 });
}

async function phone(browser: Browser) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true,
    baseURL: "http://localhost:3101",
  });
  return { ctx, page: await ctx.newPage() };
}

/** A box lies whole in the viewport, above the message box, and inside the
 *  dock's body. */
async function inView(page: Page, button: ReturnType<Page["locator"]>) {
  const vp = page.viewportSize()!;
  const s = (await button.boundingBox())!;
  const b = (await page.locator("[data-dock-body]").boundingBox())!;
  const c = (await box(page).boundingBox())!;
  expect(s.y).toBeGreaterThanOrEqual(Math.max(0, b.y) - 1);
  expect(s.y + s.height).toBeLessThanOrEqual(b.y + b.height + 1);
  expect(s.y + s.height).toBeLessThanOrEqual(c.y);
  expect(s.y + s.height).toBeLessThanOrEqual(vp.height);
}

/**
 * D3 and rule 9: a long email in Compact shows Review, never Send. Review
 * opens Expanded, where "Send to 5" shows pinned with no scroll. The fade
 * shows while more is below and goes at the end, and Send does not move.
 */
async function longEmailFlow(page: Page) {
  const body = page.locator("[data-dock-body]");
  await page.locator('[role="tab"]').nth(1).click();
  const review = open(page, "[data-dock-review]");
  await expect(review).toBeVisible({ timeout: 20_000 });
  // A hidden commit is no commit: no Send exists to reach by keyboard.
  await expect(open(page, "[data-reply-send]")).toHaveCount(0);
  await expect(open(page, "[data-mail-line]")).toHaveText("To Kim, Ravi Kumar, Lena · Cc 2 · Rates for May");
  await inView(page, review);
  await review.click();
  const send = open(page, "[data-reply-send]");
  await expect(send).toHaveText("Send to 5");
  expect(await body.evaluate((el) => el.scrollTop)).toBe(0);
  await inView(page, send);
  await expect(page.locator("[data-dock-fade]")).toBeVisible();
  const s = (await send.boundingBox())!;
  await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
  await expect(page.locator("[data-dock-fade]")).toHaveCount(0);
  await inView(page, send);
  const after = (await send.boundingBox())!;
  // The row stays at the foot. At the very end it may rise by the lines under it.
  expect(after.y).toBeLessThanOrEqual(s.y + 1);
}

test.describe("the waiting dock (WD-1)", () => {
  test.describe.configure({ timeout: 180_000 });

  for (const size of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }]) {
    test(`D3: a long email at ${size.width} shows Review in Compact and "Send to 5" pinned in Expanded`, async ({ page }) => {
      await page.setViewportSize(size);
      await start(page, { cards: "all", long: true });
      await longEmailFlow(page);
      // An untick in Expanded holds through Collapse (Esc) and Expand.
      await page.locator('[data-reply-person="geo@acme.test"] input[type="checkbox"]').uncheck();
      await expect(open(page, "[data-reply-send]")).toHaveText("Send to 4");
      await page.keyboard.press("Escape");
      await expect(open(page, "[data-mail-line]")).toHaveText("To Kim, Ravi Kumar, Lena · Cc 1 · 1 left out · Rates for May");
      await open(page, "[data-dock-review]").click();
      await expect(open(page, "[data-reply-send]")).toHaveText("Send to 4");
      expect(await answers(page)).toEqual([]);
    });
  }

  test("the consent rule: a short email shows Review in Compact too, and no Send", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await page.locator('[role="tab"]').nth(1).click();
    const review = open(page, "[data-dock-review]");
    await expect(review).toBeVisible({ timeout: 20_000 });
    await inView(page, review);
    await expect(open(page, "[data-reply-send]")).toHaveCount(0);
  });

  for (const how of ["double click", "held Enter", "Space"] as const) {
    test(`the fresh press: a ${how} on Review commits nothing`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await start(page, { cards: "all" });
      await page.locator('[role="tab"]').nth(1).click();
      const review = open(page, "[data-dock-review]");
      await expect(review).toBeVisible({ timeout: 20_000 });
      if (how === "double click") {
        await review.dblclick();
      } else if (how === "held Enter") {
        await review.focus();
        for (let i = 0; i < 12; i++) {
          await page.keyboard.down("Enter");
          await page.waitForTimeout(80);
        }
        await page.keyboard.up("Enter");
      } else {
        await review.focus();
        await page.keyboard.press(" ");
        await page.keyboard.press(" ");
      }
      await expect(open(page, "[data-reply-send]")).toHaveText("Send to 1");
      // The focus is in the dock's content, never on a commit button.
      expect(await page.evaluate(() => document.activeElement?.hasAttribute("data-dock-body"))).toBe(true);
      await page.waitForTimeout(800);
      expect(await answers(page)).toEqual([]);
    });
  }

  test("the fresh press: a click on a commit in the first 400 ms after Expanded opens does nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await expect(open(page, "[data-dock-review]")).toBeVisible({ timeout: 20_000 });
    // One mounted card (rule b): the same element before and after Expand.
    const card = await page.evaluateHandle(() => document.querySelector("[data-dock-body] [data-confirmation-card]"));
    await page.waitForTimeout(600); // long past the arming of the mount
    await open(page, "[data-dock-review]").click();
    expect(await page.evaluate((el) => el === document.querySelector("[data-dock-body] [data-confirmation-card]"), card)).toBe(true);
    // At once: the card is not armed again yet.
    await open(page, "[data-confirmation-approve]").waitFor({ state: "attached", timeout: 300 });
    await page.evaluate(() => (document.querySelector("[data-confirmation-approve]") as HTMLElement).click());
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
    // A fresh press after the wait commits.
    await open(page, "[data-confirmation-approve]").click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
  });

  test("Review, edit, Collapse, Review, commit: each card posts exactly what the screen showed", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all", withReply: true, long: true });
    const tabs = page.locator('[data-dock-body] [role="tab"]');
    await expect(tabs).toHaveCount(4, { timeout: 20_000 });
    const reviewAgain = async () => {
      await open(page, "[data-dock-review]").click();
      await page.waitForTimeout(500); // the card arms again after Expanded opens
    };
    // The new mail: a new subject and an untick.
    await tabs.nth(1).click();
    await reviewAgain();
    await page.getByRole("textbox", { name: "Subject" }).fill("Rates for June");
    await page.locator('[data-reply-person="geo@acme.test"] input[type="checkbox"]').uncheck();
    await page.keyboard.press("Escape");
    await expect(open(page, "[data-mail-line]")).toContainText("Rates for June");
    await reviewAgain();
    await expect(page.getByRole("textbox", { name: "Subject" })).toHaveValue("Rates for June");
    await expect(open(page, "[data-reply-send]")).toHaveText("Send to 4");
    await open(page, "[data-reply-send]").click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    const mailAnswer = (await answers(page))[0];
    expect(mailAnswer.request_id).toBe(MAIL);
    expect(mailAnswer.answer).toContain("Rates for June");
    expect(mailAnswer.answer).not.toContain("geo@acme.test");
    // The reply: an edited text.
    await tabs.filter({ hasText: /Reply/ }).first().click();
    await reviewAgain();
    await page.getByRole("textbox", { name: "Reply text" }).fill("Hi Kim,\n\nYes, Friday works.");
    await page.keyboard.press("Escape");
    await reviewAgain();
    await expect(page.getByRole("textbox", { name: "Reply text" })).toHaveValue("Hi Kim,\n\nYes, Friday works.");
    await open(page, "[data-reply-send]").click();
    await expect.poll(async () => (await answers(page)).length).toBe(2);
    expect((await answers(page))[1].answer).toContain("Yes, Friday works.");
    // The task: a new due date.
    await tabs.filter({ hasText: /Task/ }).first().click();
    await reviewAgain();
    await page.locator("[data-task-edit-due]").fill("2027-01-15");
    await page.keyboard.press("Escape");
    await reviewAgain();
    await expect(page.locator("[data-task-edit-due]")).toHaveValue("2027-01-15");
    await open(page, "[data-confirmation-approve]").click();
    await expect.poll(async () => (await answers(page)).length).toBe(3);
    expect((await answers(page))[2].answer).toContain("2027-01-15");
  });

  test("Esc closes Expanded from the message box, and does not stop the run; a second Esc does", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await page.locator('[data-dock-body] [role="tab"]').nth(1).click();
    await open(page, "[data-dock-review]").click();
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "on");
    await box(page).focus();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "off");
    await page.waitForTimeout(400);
    expect(await page.evaluate(() => window.__cancels)).toBe(0);
    await page.keyboard.press("Escape");
    await expect.poll(() => page.evaluate(() => window.__cancels)).toBeGreaterThan(0);
  });

  test("D4: a new question never takes the open tab", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all", lateQuestion: true });
    const tabs = page.locator('[data-dock-body] [role="tab"]');
    await expect(tabs).toHaveCount(2, { timeout: 20_000 });
    const open = await tabs.evaluateAll((els) => els.findIndex((e) => e.getAttribute("aria-selected") === "true"));
    const openTitle = await tabs.nth(open).getAttribute("title");
    await page.evaluate(() => window.__releaseQ());
    await expect(tabs).toHaveCount(3);
    // The question is the first tab now, and the open tab is the same item.
    const selected = page.locator('[data-dock-body] [role="tab"][aria-selected="true"]');
    await expect(selected).toHaveAttribute("title", openTitle ?? "");
    await expect(tabs.nth(0)).toHaveAttribute("data-unseen", "");
  });

  test("D3: a long email on a phone, keyboard down", async ({ browser }) => {
    const { ctx, page } = await phone(browser);
    try {
      await start(page, { cards: "all", long: true });
      await longEmailFlow(page);
    } finally {
      await ctx.close();
    }
  });

  test("the dock is outside the form: Enter in a card field and an option tap send nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toBeVisible({ timeout: 20_000 });
    expect(await dock(page).evaluate((el) => !!el.closest("form"))).toBe(false);

    // The new-mail card, Expanded: Enter in its Subject.
    await page.locator('[role="tab"]').nth(1).click();
    await page.locator("[data-dock-expand]").click();
    await page.getByRole("textbox", { name: "Subject" }).press("Enter");
    // The task card: Enter in its due date.
    await page.locator('[role="tab"]').nth(2).click();
    await page.locator("[data-task-edit-due]").press("Enter");
    // The question: Enter in its own answer field.
    await page.locator('[role="tab"]').nth(0).click();
    await page.getByRole("textbox", { name: "Your own answer" }).press("Enter");
    await page.waitForTimeout(800);
    expect(await answers(page)).toEqual([]);
    expect(await sends(page)).toEqual(["Plan the June run."]);

    // An option tap with words in the box: nothing goes, and the words stay.
    await box(page).fill("my own draft words");
    await page.getByRole("button", { name: /North depot/ }).click();
    await page.waitForTimeout(800);
    expect(await answers(page)).toEqual([]);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my own draft words");
  });

  test("the member's own Fold wins after a card field had the focus, by keyboard and by mouse", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
    await page.locator('[role="tab"]').nth(0).click();
    const own = page.getByRole("textbox", { name: "Your own answer" });
    // Keyboard: from the field to Fold, then Enter.
    await own.click();
    await page.locator("[data-dock-fold]").focus();
    await page.keyboard.press("Enter");
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    await page.locator("[data-dock-unfold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    // Mouse: from the field straight to Fold, at once.
    await own.click();
    await page.locator("[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded", { timeout: 300 });
    expect(await answers(page)).toEqual([]);
  });

  test("WD-D2: the question in the dock has no X, and its Not now stays; a tab press holds", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toBeVisible({ timeout: 20_000 });
    const tabs = page.locator('[data-dock-body] [role="tab"]');
    await tabs.nth(0).click();
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("[data-dock-body] [data-elicitation-card]")).toBeVisible();
    // WD-D2: no X on the question in the dock. Not now stays.
    await expect(page.locator("[data-dock-body] [data-not-now-close]")).toHaveCount(0);
    await expect(page.locator("[data-dock-body] [data-elicitation-card]").getByRole("button", { name: "Not now" })).toBeVisible();
    // The member picks the email: it stays the open tab.
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  });

  test("the fold row opens the card and approves nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
    await page.locator('[data-dock-body] [role="tab"]').nth(1).click();
    await page.locator("[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    const review = page.locator("[data-dock-unfold]");
    // The email tab is open: the fold row's one button only opens the card.
    await expect(review).toHaveText("Review");
    await review.click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
  });

  test("K6: on a desktop, the box takes the focus back when the last item leaves", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await expect(dock(page)).toBeVisible({ timeout: 20_000 });
    // The consent rule: Approve is only in Expanded, after Review.
    await open(page, "[data-dock-review]").click();
    const approve = open(page, "[data-confirmation-approve]");
    await page.waitForTimeout(600); // the card arms again after 400 ms
    await approve.click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await expect(dock(page)).toHaveCount(0, { timeout: 10_000 });
    await expect(box(page)).toBeFocused();
  });

  test("K1, K2, K3 on a phone: an arrival drops an idle keyboard, a card field keeps the dock open, one tap on Submit sends", async ({ browser }) => {
    const { ctx, page } = await phone(browser);
    try {
      await start(page, { cards: "all", gated: true });
      // K1 and K2: the member does not type, and the box has the focus.
      await box(page).focus();
      await page.evaluate(() => window.__release());
      await expect(dock(page)).toBeVisible({ timeout: 20_000 });
      await expect(box(page)).not.toBeFocused();

      // K3: a tap on the question's own field, then the keyboard comes up.
      await page.locator('[role="tab"]').nth(0).tap();
      const own = page.getByRole("textbox", { name: "Your own answer" });
      await own.tap();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
      await own.pressSequentially("via the ring road");
      // iOS: a tapped button takes no focus, and the field blurs with no
      // target as the press goes down. Without the press guard of K3 the dock
      // folds under the finger, and the tap lands on nothing.
      await page.evaluate(() => {
        document.addEventListener("mousedown", (e) => {
          if (!(e.target as HTMLElement | null)?.closest?.("button")) return;
          e.preventDefault();
          (document.activeElement as HTMLElement | null)?.blur();
        }, true);
      });
      // One tap on Submit sends the answer.
      await page.locator("[data-elicitation-submit]").tap();
      await expect.poll(async () => (await answers(page)).map((a) => a.request_id)).toContain("q".repeat(32));

      // K7: the keyboard up with no field of the card in focus folds the dock.
      await box(page).focus();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    } finally {
      await ctx.close();
    }
  });

  // ── Round 7: a commit is pressed only on a card that the member has seen,
  // whole and still, for 400 ms, with a press that began after that. ─────

  test("F1: a fold leaves Expanded, so a click at once after the unfold commits nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await open(page, "[data-dock-review]").click({ timeout: 20_000 });
    await page.waitForTimeout(600);
    await page.locator("[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    await page.locator("[data-dock-unfold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    await clickNow(page, "[data-confirmation-approve]");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
    // The unfold shows Compact: Review, and no commit.
    await expect(open(page, "[data-dock-review]")).toBeVisible();
  });

  test("F1: a new arrival unfolds the dock, and a click at once commits nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all", lateQuestion: true });
    await page.locator('[data-dock-body] [role="tab"]').nth(1).click({ timeout: 20_000 });
    await open(page, "[data-dock-review]").click();
    await page.waitForTimeout(600);
    await page.locator("[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    await page.evaluate(() => window.__releaseQ());
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    await clickNow(page, "[data-reply-send]");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
  });

  test("F1: on a phone, the keyboard up then down leaves no armed commit", async ({ browser }) => {
    const { ctx, page } = await phone(browser);
    try {
      await start(page, { cards: "one" });
      await open(page, "[data-dock-review]").tap({ timeout: 20_000 });
      await page.waitForTimeout(600);
      await box(page).focus();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
      await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.__kb(0); });
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
      await clickNow(page, "[data-confirmation-approve]");
      await page.waitForTimeout(600);
      expect(await answers(page)).toEqual([]);
    } finally {
      await ctx.close();
    }
  });

  test("F1: a press that went down before the arm commits nothing when it comes up after it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await open(page, "[data-dock-review]").click({ timeout: 20_000 });
    const approve = open(page, "[data-confirmation-approve]");
    const b = (await approve.boundingBox())!;
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(700);
    await page.mouse.up();
    await page.waitForTimeout(400);
    expect(await answers(page)).toEqual([]);
    // A fresh press commits.
    await approve.click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
  });

  test("F1: a question's Submit is not armed at once after an unfold", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await page.locator('[data-dock-body] [role="tab"]').nth(0).click({ timeout: 20_000 });
    await page.getByRole("button", { name: /North depot/ }).click();
    await page.locator("[data-dock-fold]").click();
    await page.locator("[data-dock-unfold]").click();
    await clickNow(page, "[data-elicitation-submit]");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
  });

  test("F2: Keep it and Delete it wait 400 ms; Esc in the question is Back; Collapse drops the question", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "earlier" });
    await open(page, "[data-dock-review]").click({ timeout: 20_000 });
    await page.waitForTimeout(600);
    await reject(page).click();
    await expect(open(page, "[data-earlier-keep]")).toBeVisible();
    // At once: the question is new, so its choices do nothing yet. A Reject
    // waits 5 s for its Undo, so the card itself is the evidence: it stays.
    await clickNow(page, "[data-earlier-keep]");
    await clickNow(page, "[data-earlier-delete]");
    await page.waitForTimeout(300);
    await expect(open(page, "[data-earlier-keep]")).toBeVisible();
    // Esc inside the question is Back, and the card stays Expanded.
    await open(page, "[data-earlier-keep]").focus();
    await page.keyboard.press("Escape");
    await expect(open(page, "[data-earlier-question]")).toHaveCount(0);
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "on");
    // Reject, Collapse, then a real double click on Review at one point.
    await page.waitForTimeout(500);
    await reject(page).click();
    await expect(open(page, "[data-earlier-keep]")).toBeVisible();
    await page.locator("[data-dock-expand]").click();
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "off");
    await open(page, "[data-dock-review]").dblclick();
    await page.waitForTimeout(300);
    // The card is still here, Expanded, with no question open.
    await expect(open(page, "[data-confirmation-approve]")).toBeVisible();
    await expect(open(page, "[data-earlier-question]")).toHaveCount(0);
    // A fresh press on a question that the member saw commits.
    await page.waitForTimeout(500);
    await reject(page).click();
    await page.waitForTimeout(500);
    await open(page, "[data-earlier-keep]").click();
    await expect(dock(page)).toHaveCount(0);
    await expect.poll(async () => (await answers(page)).map((a) => a.answer), { timeout: 15_000 }).toEqual(["REJECT"]);
  });

  test("F3: a held Enter on the last Approve never sends the text in the box", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await expect(open(page, "[data-dock-review]")).toBeVisible({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await open(page, "[data-dock-review]").click();
    await page.waitForTimeout(600);
    await open(page, "[data-confirmation-approve]").focus();
    for (let i = 0; i < 15; i++) {
      await page.keyboard.down("Enter");
      await page.waitForTimeout(80);
    }
    await page.keyboard.up("Enter");
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await expect(dock(page)).toHaveCount(0, { timeout: 10_000 });
    await page.waitForTimeout(300);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my unsent words");
    // K6 after the key came up: the box has the focus.
    await expect(box(page)).toBeFocused();
  });

  test("F3: an Enter in the box within 400 ms of a dock commit sends nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "one" });
    await expect(open(page, "[data-dock-review]")).toBeVisible({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await open(page, "[data-dock-review]").click();
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      (document.querySelector('[data-dock-body] [role="tabpanel"] [data-confirmation-approve]') as HTMLElement).click();
      const b = document.querySelector<HTMLTextAreaElement>('textarea[aria-label^="Message "]')!;
      b.focus();
      b.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    });
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await page.waitForTimeout(300);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my unsent words");
  });

  for (const path of [
    { card: "reply", act: "send" },
    { card: "reply", act: "save" },
    { card: "new mail", act: "send" },
  ] as const) {
    test(`F4: a ${path.card} keeps its untick and its edit across a question tab, Collapse and a fold; ${path.act} posts the screen`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await start(page, { cards: "all", withReply: true });
      const tabs = page.locator('[data-dock-body] [role="tab"]');
      await expect(tabs).toHaveCount(4, { timeout: 20_000 });
      const reply = path.card === "reply";
      const pick = () => tabs.nth(reply ? 3 : 1);
      const gone = reply ? "lee@contoso.test" : "kim@contoso.test";
      const field = reply ? "Reply text" : "Email text";
      const text = "Hi Kim,\n\nYes, Friday works.";
      await pick().click();
      await open(page, "[data-dock-review]").click();
      await page.waitForTimeout(500);
      if (!reply) {
        // The new mail goes to Kim only: tick nobody off it, edit the subject.
        await page.getByRole("textbox", { name: "Subject" }).fill("Rates for June");
      } else {
        await open(page, `[data-reply-person="${gone}"] input[type="checkbox"]`).uncheck();
      }
      await page.getByRole("textbox", { name: field }).fill(text);
      // Another tab, then back.
      await tabs.nth(0).click();
      await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
      await pick().click();
      // Collapse and Review, then a fold and an unfold, then Review.
      if (await page.locator('[data-dock-expand="on"]').count()) await page.keyboard.press("Escape");
      await open(page, "[data-dock-review]").click();
      await page.locator("[data-dock-fold]").click();
      await page.locator("[data-dock-unfold]").click();
      await open(page, "[data-dock-review]").click();
      await page.waitForTimeout(500);
      // The screen.
      await expect(page.getByRole("textbox", { name: field })).toHaveValue(text);
      if (reply) {
        await expect(open(page, `[data-reply-person="${gone}"] input[type="checkbox"]`)).not.toBeChecked();
        await expect(open(page, "[data-reply-send]")).toHaveText("Send to 1");
      } else {
        await expect(page.getByRole("textbox", { name: "Subject" })).toHaveValue("Rates for June");
      }
      await open(page, path.act === "send" ? "[data-reply-send]" : "[data-reply-save]").click();
      await expect.poll(async () => (await answers(page)).length).toBe(1);
      const posted = (await answers(page))[0].answer;
      expect(posted).toContain("Yes, Friday works.");
      if (reply) expect(posted).not.toContain(gone);
      else expect(posted).toContain("Rates for June");
    });
  }

  test("F5: a redraft never takes the open tab from a question", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    const tabs = page.locator('[data-dock-body] [role="tab"]');
    await expect(tabs).toHaveCount(3, { timeout: 20_000 });
    await tabs.nth(0).click();
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    // A chat message closed the email (a slot), and its new draft takes the slot.
    await page.evaluate(({ mail, form }) => {
      window.__push({ type: "custom", name: "confirmation_resolved", value: { request_id: mail, answer: "REVISED" } });
      window.__push({ type: "custom", name: "confirmation_requested", value: {
        title: "Send this new email?", detail: "From Work · Rates for May", request_id: "b".repeat(32),
        reply: { ...form, body: "Hi Kim,\n\nThe new rates are attached.", revises: mail, draft: 2 },
      } });
    }, { mail: MAIL, form: FORM });
    await page.waitForTimeout(1200);
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
  });

  test("F6: Esc after a click on blank thread closes Expanded, and never over a modal", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await page.locator('[data-dock-body] [role="tab"]').nth(1).click({ timeout: 20_000 });
    const blank = async () => {
      const r = await page.evaluate(() => {
        const t = document.querySelector("[data-dock-host]")!.previousElementSibling!.getBoundingClientRect();
        return { x: t.left + 3, y: t.top + t.height / 2 };
      });
      await page.mouse.click(r.x, r.y);
      expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
    };
    await open(page, "[data-dock-review]").click();
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "on");
    await blank();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "off");
    expect(await page.evaluate(() => window.__cancels)).toBe(0);
    // A modal above the chat owns its Esc.
    await open(page, "[data-dock-review]").click();
    await blank();
    await page.evaluate(() => {
      const m = document.createElement("div");
      m.setAttribute("role", "dialog");
      m.setAttribute("aria-modal", "true");
      document.body.appendChild(m);
    });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    await expect(page.locator("[data-dock-expand]")).toHaveAttribute("data-dock-expand", "on");
  });
});
