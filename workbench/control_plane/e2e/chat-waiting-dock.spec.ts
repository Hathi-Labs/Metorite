import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * The waiting dock in a real browser (WD-1, `projects_ai_chat.md` §24.11, D9).
 *
 * D9 (2026-11-27): the dock holds the two question cards (`ask_questions` and
 * `ask_user`) and ONE pointer row while an approval waits. Every approval
 * card draws in the thread, as with the flag off. The pointer's Review only
 * scrolls the thread to the card, and moves the focus to its heading.
 *
 * The dock sits between the thread and the composer form, never inside it.
 * Inside the form, a button of a card is a submit button: Enter in a card
 * field sent the message box (verifier P0, 2026-11-27).
 *
 * No gateway runs. The page's `fetch` is stubbed: the shell read says
 * `waiting_dock: true` (or false), and the chat stream holds the cards of
 * each scenario and stays live. `window.__answers` records each respond-input
 * and `window.__sends` each chat message. `window.__kb(px)` raises a fake
 * soft keyboard of `px` pixels.
 */

const PID = "11111111-1111-1111-1111-111111111111";
const MAIL = "a".repeat(32);
const TASK = "c".repeat(32);
const TASK2 = "d".repeat(32);
const ASK = "q".repeat(32);
const USER = "u".repeat(32);
const PEOPLE = { to: ["kim@contoso.test"], cc: [] };
const FORM = {
  kind: "new", message_id: "", mailbox: "Work · jane@acme.test", subject: "Rates for May",
  body: "Hi Kim,\n\nThe rates are attached.", recipients: { reply: PEOPLE, reply_all: PEOPLE },
  files: [], notices: [], note: "",
};
// A long email: the card is far taller than the thread's room.
const LONG_FORM = {
  ...FORM,
  body: ["Hi Kim,", "", ...Array.from({ length: 80 }, (_, i) => `Line ${i + 1} of the May rates, with the detail of each change.`), "", "Best,", "Jane"].join("\n"),
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
    __kb: (px: number) => void;
    __push: (e: unknown) => void;
  }
}

/**
 * The cards of a scenario:
 * - `questions`: the `ask_questions` card and the `ask_user` card;
 * - `q1`: the `ask_questions` card only;
 * - `longQ`: a long `ask_questions` card and the `ask_user` card;
 * - `approvals`: a task, a new mail and a task, then the `ask_questions` card;
 * - `one`: one task approval only;
 * - `longMail`: one long new mail only;
 * - `remote`: a new mail that does not block the run (no request id), then
 *   the `ask_questions` card. A run end takes the question and leaves the mail.
 */
type Cards = "questions" | "q1" | "longQ" | "approvals" | "one" | "longMail" | "remote";

async function install(page: Page, opts: { cards: Cards; gated?: boolean; dock?: boolean }) {
  await page.addInitScript(({ cards, gated, dock, form, longForm, values, pid, mail, task, task2, ask, user }) => {
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
    let live: ReadableStreamDefaultController<Uint8Array> | null = null;
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const send = (e: unknown) => { try { live?.enqueue(frame(e)); } catch { /* closed */ } };
    w.__push = send;
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    const askQuestions = (long: boolean) => ({
      type: "custom", name: "elicitation_requested", value: {
        request_id: ask,
        questions: [{
          header: "Depot",
          question: long
            ? `Which depot should the June run ship from? ${"Each depot has its own rates, its own trucks and its own hours, so read each option. ".repeat(6)}`
            : "Which depot should the June run ship from?",
          multiSelect: false, allowFreeformInput: true,
          options: long
            ? Array.from({ length: 14 }, (_, i) => ({ label: `Depot ${i + 1}`, description: "Ships on weekdays from 06:00 to 18:00." }))
            : [{ label: "North depot" }, { label: "South depot" }],
        }],
      },
    });
    const askUser = {
      type: "custom", name: "user_input_requested",
      value: { request_id: user, question: "Which day should the run start?", choices: ["Monday", "Tuesday"], allowFreeform: false },
    };
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/auth/me/shell")) {
        return json({ answered: "answered", preset: null, pins: null, known: null, waiting_dock: dock });
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
      if (url.includes("/api/agent/cancel")) return json({ cancelled: true });
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
            // A long answer, so the thread scrolls.
            const lines = Array.from({ length: 40 }, (_, i) => `Step ${i + 1} of the plan for the June run.`).join("\n\n");
            send({ type: "delta", messageId: "m-1", content: `Here is what I need from you.\n\n${lines}` });
            if (gated) await released;
            const task1 = { type: "custom", name: "confirmation_requested", value: {
              title: "Create this task?", detail: "«Ship the pack» · Launch", request_id: task,
            } };
            if (cards === "one") { send(task1); return; }
            if (cards === "remote") {
              send({ type: "message_end", messageId: "m-1" });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Send this new email?", detail: "From Work · Rates for May", reply: form,
              } });
              send(askQuestions(false));
              return;
            }
            if (cards === "longMail") {
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Send this new email?", detail: "From Work · Rates for May", request_id: mail, reply: longForm,
              } });
              return;
            }
            if (cards === "approvals") {
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Create this task?", detail: "«Ship the pack» · in Launch · status Doing",
                context: "Check each field, then approve.\nproject: «Launch»\nname: «Ship the pack»\ndue_at: 2026-12-01",
                fenced: true, request_id: task, edit: { kind: "create_task", project_id: pid, values },
              } });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Send this new email?", detail: "From Work · Rates for May", request_id: mail, reply: form,
              } });
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Create this task?", detail: "«Order filament» · Launch", request_id: task2,
              } });
              send(askQuestions(false));
              return;
            }
            send(askQuestions(cards === "longQ"));
            if (cards !== "q1") send(askUser);
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, {
    cards: opts.cards, gated: !!opts.gated, dock: opts.dock !== false, form: FORM, longForm: LONG_FORM, values: VALUES,
    pid: PID, mail: MAIL, task: TASK, task2: TASK2, ask: ASK, user: USER,
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
const pointer = (page: Page) => page.locator("[data-dock-pointer]");
const review = (page: Page) => page.locator("[data-dock-pointer-review]");
const dockTabs = (page: Page) => page.locator('[data-dock-body] [role="tab"]');
/** A control of the OPEN question only: every question card stays mounted. */
const open = (page: Page, sel: string) => page.locator(`[data-dock-body] [data-dock-question]:not([hidden]) ${sel}`);
/** A programmatic click on each control of the open question, at once. */
const clickNow = (page: Page, sel: string) =>
  page.evaluate((s) => document.querySelectorAll<HTMLElement>(`[data-dock-body] [data-dock-question]:not([hidden]) ${s}`).forEach((b) => b.click()), sel);
/** The approval card in the thread. */
const threadCard = (page: Page) => page.locator("[data-chat-ask] [data-confirmation-card]");

async function start(page: Page, opts: { cards: Cards; gated?: boolean; dock?: boolean }) {
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

/** Scroll the thread to its top, so the card in the thread is out of view. */
async function threadToTop(page: Page) {
  await page.evaluate(() => {
    const t = document.querySelector("[data-dock-host]")!.previousElementSibling as HTMLElement;
    t.scrollTop = 0;
  });
}

/** Where the card is, against the thread's room and the dock's top. */
async function cardPlace(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<HTMLElement>("[data-dock-host]")!;
    const t = host.previousElementSibling as HTMLElement;
    const card = document.querySelector<HTMLElement>("[data-chat-ask] [data-confirmation-card]")!;
    const tr = t.getBoundingClientRect();
    const portTop = tr.top + t.clientTop;
    const c = card.getBoundingClientRect();
    const h = card.querySelector("[data-rollup-title]")!.getBoundingClientRect();
    return {
      cardTop: c.top, cardBottom: c.bottom, cardH: c.height, headTop: h.top, headBottom: h.bottom,
      portTop, portBottom: portTop + t.clientHeight, portH: t.clientHeight,
      dockTop: host.getBoundingClientRect().top,
      focusInCardHead: !!document.activeElement?.closest("[data-confirmation-card]") && document.activeElement?.hasAttribute("data-rollup-title"),
      focusTag: document.activeElement?.tagName ?? "",
    };
  });
}

test.describe("the waiting dock (WD-1, D9)", () => {
  test.describe.configure({ timeout: 180_000 });

  // ── D9: the approvals stay in the thread, and one pointer names them ────

  test("D9: approvals mount one card at a time in the thread, and the pointer names the open tab 'and 2 more'", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "approvals" });
    await expect(pointer(page)).toBeVisible({ timeout: 20_000 });
    // ONE mounted approval card, in the thread, with its three tabs.
    await expect(page.locator("[data-confirmation-card]")).toHaveCount(1);
    await expect(threadCard(page)).toHaveCount(1);
    await expect(page.locator("[data-waiting-dock] [data-confirmation-card]")).toHaveCount(0);
    const threadTabs = page.locator('[data-chat-ask] [data-confirmation-card] [role="tab"]');
    await expect(threadTabs).toHaveCount(3);
    // The pointer names the card the queue shows, its open tab (2026-11-28).
    await expect(threadTabs.nth(0)).toHaveAttribute("aria-selected", "true");
    const first = (await threadTabs.nth(0).innerText()).trim();
    await expect(page.locator("[data-dock-pointer-label]")).toContainText(first);
    await expect(pointer(page)).toContainText("Waits for you:");
    await expect(pointer(page)).toContainText("and 2 more");
    // A change of tab in the thread still mounts one card, and the pointer
    // follows it.
    await threadTabs.nth(1).click();
    await expect(page.locator("[data-confirmation-card]")).toHaveCount(1);
    const second = (await threadTabs.nth(1).innerText()).trim();
    await expect(page.locator("[data-dock-pointer-label]")).toContainText(second);
    await expect(pointer(page)).toContainText("and 2 more");
    await expect(page.locator("[data-confirmation-card] [data-reply-send]")).toHaveCount(1);
    // The dock holds the question and no approval tab.
    await expect(dockTabs(page)).toHaveCount(0);
    await expect(page.locator("[data-dock-body] [data-elicitation-card]")).toBeVisible();
    // The count of all that waits: 3 approvals and 1 question.
    await expect(page.locator("[data-dock-thread-line]")).toContainText("Waiting for you: 4 items, below");
    await expect(page.locator("[data-dock-live]")).toHaveText("4 items wait for you");
    // Review only scrolls: the tab the member opened stays open, its edit
    // stays, and still one card is mounted.
    await page.getByRole("textbox", { name: "Subject" }).fill("Rates for June");
    await threadToTop(page);
    await review(page).click();
    await expect(threadTabs.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("textbox", { name: "Subject" })).toHaveValue("Rates for June");
    await expect(page.locator("[data-confirmation-card]")).toHaveCount(1);
    await page.waitForTimeout(400);
    const p = await cardPlace(page);
    expect(p.focusInCardHead).toBe(true);
    if (p.cardH > p.portH) expect(Math.abs(p.cardTop - p.portTop)).toBeLessThanOrEqual(4);
    else {
      expect(p.cardTop).toBeGreaterThanOrEqual(p.portTop - 1);
      expect(p.cardBottom).toBeLessThanOrEqual(p.dockTop + 1);
    }
    expect(await answers(page)).toEqual([]);
  });

  test("D9: only approvals wait, so the dock is the pointer row alone", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "one" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "pointer", { timeout: 20_000 });
    await expect(page.locator("[data-dock-body]")).toHaveCount(0);
    await expect(pointer(page)).toContainText("Waits for you:");
    await expect(pointer(page)).not.toContainText("more");
    await expect(threadCard(page)).toHaveCount(1);
    // The AskPin draws no second pointer for what the row names.
    await expect(page.locator("[data-ask-pin]")).toHaveCount(0);
    await threadToTop(page);
    await page.waitForTimeout(500);
    await expect(page.locator("[data-ask-pin]")).toHaveCount(0);
  });

  test("D9: Review scrolls the card into full view above the dock and focuses its heading", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "one" });
    await expect(review(page)).toBeVisible({ timeout: 20_000 });
    await threadToTop(page);
    const before = await cardPlace(page);
    expect(before.cardTop).toBeGreaterThan(before.portBottom);
    await review(page).click();
    // Settle, then the place must hold: the thread does not run back.
    await page.waitForTimeout(600);
    const after = await cardPlace(page);
    expect(after.cardTop).toBeGreaterThanOrEqual(after.portTop - 1);
    expect(after.cardBottom).toBeLessThanOrEqual(after.portBottom + 1);
    expect(after.cardBottom).toBeLessThanOrEqual(after.dockTop + 1);
    expect(after.focusInCardHead).toBe(true);
    expect(after.focusTag).not.toBe("BUTTON");
    expect(await answers(page)).toEqual([]);
  });

  test("D9: Enter on the pointer, held, only scrolls: nothing commits and the box sends nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "one" });
    await expect(review(page)).toBeVisible({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await threadToTop(page);
    await review(page).focus();
    for (let i = 0; i < 12; i++) {
      await page.keyboard.down("Enter");
      await page.waitForTimeout(60);
    }
    await page.keyboard.up("Enter");
    await page.keyboard.press("Space");
    await page.waitForTimeout(800);
    expect(await answers(page)).toEqual([]);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my unsent words");
    await expect(threadCard(page)).toBeVisible();
    const p = await cardPlace(page);
    expect(p.focusInCardHead).toBe(true);
  });

  for (const size of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    test(`D9: a card taller than the room shows its TOP after Review, at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size);
      await start(page, { cards: "longMail" });
      await expect(review(page)).toBeVisible({ timeout: 20_000 });
      await threadToTop(page);
      await review(page).click();
      await page.waitForTimeout(600);
      const p = await cardPlace(page);
      expect(p.cardH).toBeGreaterThan(p.portH);
      // The top of the card at the top of the room (a sub-pixel or two of
      // rounding), and its heading whole in view.
      expect(Math.abs(p.cardTop - p.portTop)).toBeLessThanOrEqual(4);
      expect(p.headTop).toBeGreaterThanOrEqual(p.portTop);
      expect(p.focusInCardHead).toBe(true);
      // From below the card too: it scrolls back up to the top.
      await page.evaluate(() => {
        const t = document.querySelector("[data-dock-host]")!.previousElementSibling as HTMLElement;
        t.scrollTop = t.scrollHeight;
      });
      await review(page).click();
      await page.waitForTimeout(600);
      const q = await cardPlace(page);
      expect(Math.abs(q.cardTop - q.portTop)).toBeLessThanOrEqual(4);
      expect(q.headTop).toBeGreaterThanOrEqual(q.portTop);
      expect(await answers(page)).toEqual([]);
    });
  }

  test("flag off: no dock, and the thread draws each card as on main", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "approvals", dock: false });
    await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
    await expect(page.locator("[data-dock-host]")).toHaveCount(0);
    await expect(dock(page)).toHaveCount(0);
    await expect(page.locator("[data-chat-ask] [data-elicitation-card]")).toBeVisible();
    await expect(page.locator("[data-not-now-close]")).toHaveCount(1);
  });

  // ── The questions in the dock ─────────────────────────────────────────

  test("two questions are two tabs, and a change of tab sets a long question's scroll to the top", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "longQ" });
    await expect(dockTabs(page)).toHaveCount(2, { timeout: 20_000 });
    await expect(page.locator("[data-dock-body] [data-card-tabs]")).toContainText("2 waiting");
    const body = page.locator("[data-dock-body]");
    expect(await body.evaluate((el) => el.scrollHeight > el.clientHeight + 20)).toBe(true);
    // 4b: the fade covers no control. Its foot is at or above the top of the
    // pinned Submit row.
    await expect(page.locator("[data-dock-fade]")).toHaveCount(1);
    const fade = (await page.locator("[data-dock-fade]").boundingBox())!;
    const pinned = await page.evaluate(() => {
      const s = document.querySelector("[data-dock-body] [data-dock-question]:not([hidden]) [data-elicitation-submit]")!;
      return s.parentElement!.getBoundingClientRect().top;
    });
    expect(fade.y + fade.height).toBeLessThanOrEqual(pinned + 0.5);
    await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    expect(await body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    await dockTabs(page).nth(1).click();
    await expect(dockTabs(page).nth(1)).toHaveAttribute("aria-selected", "true");
    expect(await body.evaluate((el) => el.scrollTop)).toBe(0);
    // Back to the long question: it opens at its top, by key too.
    await body.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await dockTabs(page).nth(1).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(dockTabs(page).nth(0)).toHaveAttribute("aria-selected", "true");
    expect(await body.evaluate((el) => el.scrollTop)).toBe(0);
  });

  test("the dock is outside the form: Enter in a question field and an option tap send nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "questions" });
    await expect(dock(page)).toBeVisible({ timeout: 20_000 });
    expect(await dock(page).evaluate((el) => !!el.closest("form"))).toBe(false);
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

  test("WD-D2: a question in the dock has no X, its Not now stays, and the fold row submits nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "questions" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
    await expect(page.locator("[data-dock-body] [data-not-now-close]")).toHaveCount(0);
    await expect(open(page, "[data-not-now]")).toBeVisible();
    await page.getByRole("button", { name: /North depot/ }).click();
    await open(page, "[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    const unfold = page.locator("[data-dock-unfold]");
    await expect(unfold).toHaveText("Answer");
    await unfold.click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
  });

  test("the member's own Fold wins after a question field had the focus, by keyboard and by mouse", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
    const own = page.getByRole("textbox", { name: "Your own answer" });
    await own.click();
    await open(page, "[data-dock-fold]").focus();
    await page.keyboard.press("Enter");
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    await page.locator("[data-dock-unfold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
    await own.click();
    await open(page, "[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded", { timeout: 300 });
    expect(await answers(page)).toEqual([]);
  });

  test("K1, K2, K3 on a phone: an arrival drops an idle keyboard, a card field keeps the dock open, one tap on Submit sends", async ({ browser }) => {
    const { ctx, page } = await phone(browser);
    try {
      await start(page, { cards: "q1", gated: true });
      await box(page).focus();
      await page.evaluate(() => window.__release());
      await expect(dock(page)).toBeVisible({ timeout: 20_000 });
      await expect(box(page)).not.toBeFocused();
      const own = page.getByRole("textbox", { name: "Your own answer" });
      await own.tap();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
      await own.pressSequentially("via the ring road");
      await page.evaluate(() => {
        document.addEventListener("mousedown", (e) => {
          if (!(e.target as HTMLElement | null)?.closest?.("button")) return;
          e.preventDefault();
          (document.activeElement as HTMLElement | null)?.blur();
        }, true);
      });
      await open(page, "[data-elicitation-submit]").tap();
      await expect.poll(async () => (await answers(page)).map((a) => a.request_id)).toContain("q".repeat(32));
    } finally {
      await ctx.close();
    }
  });

  test("K7 on a phone: the keyboard up with no card field in focus folds the dock", async ({ browser }) => {
    const { ctx, page } = await phone(browser);
    try {
      await start(page, { cards: "questions" });
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
      await box(page).focus();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    } finally {
      await ctx.close();
    }
  });

  // ── Round 7, kept for the questions: a commit is pressed only on a card
  // that the member has seen, whole and still, for 400 ms, with a press
  // that began after that. ──────────────────────────────────────────────

  test("round 7: a question's Submit is not armed at once after an unfold", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await page.getByRole("button", { name: /North depot/ }).click({ timeout: 20_000 });
    await page.waitForTimeout(600);
    await open(page, "[data-dock-fold]").click();
    await page.locator("[data-dock-unfold]").click();
    await clickNow(page, "[data-elicitation-submit]");
    await page.waitForTimeout(600);
    expect(await answers(page)).toEqual([]);
  });

  for (const how of ["click", "ArrowLeft"] as const) {
    test(`round 7: a change of tab by ${how} arms the new card again`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await start(page, { cards: "questions" });
      await expect(dockTabs(page)).toHaveCount(2, { timeout: 20_000 });
      await page.getByRole("button", { name: /North depot/ }).click();
      await dockTabs(page).nth(1).click();
      await page.waitForTimeout(600);
      if (how === "click") await dockTabs(page).nth(0).click();
      else {
        await dockTabs(page).nth(1).focus();
        await page.keyboard.press("ArrowLeft");
      }
      await expect(dockTabs(page).nth(0)).toHaveAttribute("aria-selected", "true");
      await clickNow(page, "[data-elicitation-submit]");
      await page.waitForTimeout(600);
      expect(await answers(page)).toEqual([]);
      // A fresh press after the arm sends, with the pick kept across the tabs.
      await open(page, "[data-elicitation-submit]").click();
      await expect.poll(async () => (await answers(page)).map((a) => a.request_id)).toEqual(["q".repeat(32)]);
    });
  }

  test("round 7: a press that went down before the arm commits nothing when it comes up after it", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await page.getByRole("button", { name: /North depot/ }).click({ timeout: 20_000 });
    await open(page, "[data-dock-fold]").click();
    await page.locator("[data-dock-unfold]").click();
    const submit = open(page, "[data-elicitation-submit]");
    const b = (await submit.boundingBox())!;
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.waitForTimeout(700);
    await page.mouse.up();
    await page.waitForTimeout(400);
    expect(await answers(page)).toEqual([]);
    await submit.click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
  });

  test("round 7: a held Enter on Submit sends once, never the box, and K6 waits for the keyup", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await page.getByRole("button", { name: /North depot/ }).click({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await page.waitForTimeout(600);
    await open(page, "[data-elicitation-submit]").focus();
    for (let i = 0; i < 15; i++) {
      await page.keyboard.down("Enter");
      await page.waitForTimeout(80);
    }
    // While the key is still down, the box has not taken the focus (K6).
    await expect(dock(page)).toHaveCount(0, { timeout: 10_000 });
    await expect(box(page)).not.toBeFocused();
    await page.keyboard.up("Enter");
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await page.waitForTimeout(300);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my unsent words");
    await expect(box(page)).toBeFocused();
  });

  test("round 7: an Enter in the box within 400 ms of a dock commit sends nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await page.getByRole("button", { name: /North depot/ }).click({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await page.waitForTimeout(600);
    await page.evaluate(() => {
      (document.querySelector("[data-dock-body] [data-elicitation-submit]") as HTMLElement).click();
      const b = document.querySelector<HTMLTextAreaElement>('textarea[aria-label^="Message "]')!;
      b.focus();
      b.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    });
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await page.waitForTimeout(300);
    expect(await sends(page)).toEqual(["Plan the June run."]);
    await expect(box(page)).toHaveValue("my unsent words");
  });

  test("K6: a letter typed with Shift never stops the box from taking the focus back", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "q1" });
    await page.getByRole("button", { name: /North depot/ }).click({ timeout: 20_000 });
    await box(page).focus();
    await page.keyboard.down("Shift");
    await page.keyboard.down("KeyP");
    await page.keyboard.up("Shift");
    await page.keyboard.up("KeyP");
    await page.waitForTimeout(600);
    await open(page, "[data-elicitation-submit]").click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await expect(dock(page)).toHaveCount(0, { timeout: 10_000 });
    await expect(box(page)).toBeFocused({ timeout: 1_000 });
  });

  test("D9: an approval answered in the thread leaves the dock's question in place", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "approvals" });
    await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
    await page.waitForTimeout(600);
    const approve = page.locator("[data-chat-ask] [data-confirmation-approve]");
    await approve.click();
    await expect.poll(async () => (await answers(page)).length).toBe(1);
    await expect(pointer(page)).toContainText("and 1 more");
    await expect(page.locator("[data-dock-body] [data-elicitation-card]")).toBeVisible();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
  });
});

// ── F1 of the verifier: nothing moves under the pointer (2026-11-28) ───────
//
// A second press after a Submit, a Not now or a remote removal landed on a
// commit control of the thread: the dock shrank at once, and the thread grew
// into the gap. Each row lines a commit control of the thread up with the
// dock control, under the dock, with the thread scrolled up from its foot.
// Then it presses twice at one point, and asserts that no approval or send
// went and that no second question was answered.

const QUESTION_IDS = new Set(["q".repeat(32), "u".repeat(32)]);

/** The device of a row: the viewport, and a mouse or a finger. */
const DEVICES = [
  { name: "390 touch", width: 390, height: 844, touch: true },
  { name: "390 mouse", width: 390, height: 844, touch: false },
  { name: "1280 mouse", width: 1280, height: 800, touch: false },
  { name: "1280 touch", width: 1280, height: 800, touch: true },
] as const;
type Device = (typeof DEVICES)[number];

async function device(browser: Browser, d: Device) {
  const ctx = await browser.newContext({
    viewport: { width: d.width, height: d.height }, hasTouch: d.touch, isMobile: d.touch && d.width < 600,
    baseURL: "http://localhost:3101",
  });
  return { ctx, page: await ctx.newPage() };
}

/** The centre of the open question's control, on the screen. */
async function centreOf(page: Page, sel: string) {
  const b = (await open(page, sel).boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

/** The commit controls of the open approval card in the thread. */
const THREAD_COMMITS = ["[data-reply-send]", "[data-reply-save]", "[data-reply-discard]", "[data-confirmation-approve]"]
  .map((c) => `[data-chat-ask] [data-confirmation-card] ${c}`)
  .join(", ");

/**
 * Line a commit control of the thread up with the open question's control
 * `sel`: pick the thread control that shares the most of its width, and
 * scroll the thread so its centre lies at the centre of `sel`. The thread
 * control is then under the dock, and the thread is up from its foot. It
 * returns the point to press, and the place of the thread control after the
 * scroll, as a check.
 */
async function lineUp(page: Page, sel: string, text = "", ownX = false) {
  return page.evaluate(([dockSel, commits, words, mine]) => {
    const t = document.querySelector("[data-dock-host]")!.previousElementSibling as HTMLElement;
    const own = Array.from(document.querySelectorAll<HTMLElement>(`[data-dock-body] [data-dock-question]:not([hidden]) ${dockSel}`))
      .find((el) => (el.textContent ?? "").includes(words as string))!
      .getBoundingClientRect();
    const overlap = (r: DOMRect) => Math.min(r.right, own.right) - Math.max(r.left, own.left);
    const pick = Array.from(document.querySelectorAll<HTMLElement>(commits))
      .filter((el) => el.offsetParent !== null)
      .sort((a, b) => overlap(b.getBoundingClientRect()) - overlap(a.getBoundingClientRect()))[0];
    const r = pick.getBoundingClientRect();
    const y = own.top + own.height / 2;
    const x = mine ? own.left + own.width / 2 : (Math.max(r.left, own.left) + Math.min(r.right, own.right)) / 2;
    t.scrollTop += r.top + r.height / 2 - y;
    const after = pick.getBoundingClientRect();
    return {
      x, y, overlap: overlap(r), name: pick.textContent?.trim() ?? "",
      lined: after.top + after.height / 2, foot: t.scrollHeight - t.clientHeight - t.scrollTop,
    };
  }, [sel, THREAD_COMMITS, text, ownX] as const);
}

/**
 * Watch the point of a press, each frame, for `ms` by the page's own clock.
 * Nothing may move under the pointer while the dock holds (800 ms). The
 * point may show the control that was there at the start, or the dock's
 * inert line or gap in its place (`[data-dock-held]`, `[data-dock-gap]`),
 * and nothing else. It records each other element, the words of each inert
 * line, and how far the top of the dock's host moved.
 */
async function watchPoint(page: Page, x: number, y: number, ms: number) {
  await page.evaluate(([px, py, span]) => {
    const w = window as unknown as { __watch: { moved: string[]; held: string[]; shift: number } };
    const CONTROL = "button, a[href], input, textarea, select, [role='tab']";
    const first = document.elementFromPoint(px, py)?.closest(CONTROL) ?? null;
    const host = document.querySelector<HTMLElement>("[data-dock-host]")!;
    const top0 = host.getBoundingClientRect().top;
    const until = performance.now() + span;
    w.__watch = { moved: [], held: [], shift: 0 };
    const tick = () => {
      if (performance.now() > until) return;
      const el = document.elementFromPoint(px, py);
      const same = !!first && !!el && first.contains(el);
      const held = el?.closest("[data-dock-held]");
      const words = held?.textContent ?? "";
      if (held && !w.__watch.held.includes(words)) w.__watch.held.push(words);
      if (el && !same && !held && !el.closest("[data-dock-gap]")) {
        const d = (el.closest(CONTROL) ?? el).outerHTML.slice(0, 90);
        if (!w.__watch.moved.includes(d)) w.__watch.moved.push(d);
      }
      w.__watch.shift = Math.max(w.__watch.shift, Math.abs(host.getBoundingClientRect().top - top0));
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [x, y, ms]);
}

/** What the watch saw. */
const watched = (page: Page) =>
  page.evaluate(() => (window as unknown as { __watch: { moved: string[]; held: string[]; shift: number } }).__watch);

async function pressAt(page: Page, d: Device, x: number, y: number) {
  if (d.touch) await page.touchscreen.tap(x, y);
  else await page.mouse.click(x, y);
}

/** What went: the approvals, the questions and the chat messages. */
async function wire(page: Page) {
  const a = await answers(page);
  return {
    approvals: a.filter((x) => !QUESTION_IDS.has(x.request_id)).map((x) => x.request_id),
    questions: a.filter((x) => QUESTION_IDS.has(x.request_id)).map((x) => x.request_id),
    sends: await sends(page),
  };
}

/** The Send of the open mail card in the thread. */
const THREAD_SEND = "[data-chat-ask] [data-confirmation-card] [data-reply-send]";

test.describe("F1: nothing moves under the pointer (WD-1, 2026-11-28)", () => {
  test.describe.configure({ timeout: 180_000 });

  for (const d of DEVICES) {
    test(`${d.name}: a double press on Submit sends the question once, and nothing of the thread`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "approvals" });
        await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
        // The thread's mail tab, so its Send is the commit under the dock.
        await page.locator('[data-chat-ask] [data-confirmation-card] [role="tab"]').nth(1).click();
        await expect(page.locator(THREAD_SEND)).toHaveCount(1);
        await open(page, "[data-elicitation-card] button:has-text('North depot')").click();
        await page.waitForTimeout(600);
        const at = await lineUp(page, "[data-elicitation-submit]");
        expect(at.overlap).toBeGreaterThan(4);
        expect(Math.abs(at.lined - at.y)).toBeLessThanOrEqual(2);
        expect(at.foot).toBeGreaterThan(0);
        await watchPoint(page, at.x, at.y, 700);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(150);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(700);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.held).toEqual(["Answer sent"]);
        expect(seen.shift).toBeLessThan(1);
        await page.waitForTimeout(1000);
        const w = await wire(page);
        expect(w.questions).toEqual(["q".repeat(32)]);
        expect(w.approvals).toEqual([]);
        expect(w.sends).toEqual(["Plan the June run."]);
        // Rule 2: after the hold, the line goes, and the pointer row stays.
        await expect(page.locator("[data-dock-held]")).toHaveCount(0);
        await expect(pointer(page)).toBeVisible();
      } finally {
        await ctx.close();
      }
    });

    test(`${d.name}: a double press on Not now declines once, and sends nothing of the thread`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "approvals" });
        await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
        await page.locator('[data-chat-ask] [data-confirmation-card] [role="tab"]').nth(1).click();
        await expect(page.locator(THREAD_SEND)).toHaveCount(1);
        await page.waitForTimeout(600);
        const at = await lineUp(page, "[data-not-now]");
        expect(at.overlap).toBeGreaterThan(4);
        expect(Math.abs(at.lined - at.y)).toBeLessThanOrEqual(2);
        await watchPoint(page, at.x, at.y, 700);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(150);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(700);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.held).toEqual(["Not now"]);
        expect(seen.shift).toBeLessThan(1);
        await page.waitForTimeout(1000);
        const w = await wire(page);
        expect(w.questions).toEqual(["q".repeat(32)]);
        expect(w.approvals).toEqual([]);
        expect(w.sends).toEqual(["Plan the June run."]);
      } finally {
        await ctx.close();
      }
    });

    test(`${d.name}: a run end takes the question, and a press 300 ms later sends nothing of the thread`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "remote" });
        await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
        await expect(page.locator(THREAD_SEND)).toHaveCount(1);
        await page.waitForTimeout(600);
        // The member goes to pick an option.
        const at = await lineUp(page, "[data-elicitation-card] button", "South depot");
        expect(at.overlap).toBeGreaterThan(4);
        expect(Math.abs(at.lined - at.y)).toBeLessThanOrEqual(2);
        // The run ends: the question leaves the dock with no press.
        await watchPoint(page, at.x, at.y, 700);
        await page.evaluate(() => window.__push({ type: "done" }));
        await page.waitForTimeout(300);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(500);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.held).toEqual(["Answered elsewhere"]);
        expect(seen.shift).toBeLessThan(1);
        await page.waitForTimeout(1100);
        const w = await wire(page);
        expect(w.questions).toEqual([]);
        expect(w.approvals).toEqual([]);
        expect(w.sends).toEqual(["Plan the June run."]);
      } finally {
        await ctx.close();
      }
    });

    test(`${d.name}: a double press on Q1's Submit while Q2 waits never answers Q2`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "questions" });
        await expect(dockTabs(page)).toHaveCount(2, { timeout: 20_000 });
        // Q2 has its pick, so its Submit is live when it shows.
        await dockTabs(page).nth(1).click();
        await open(page, "button:has-text('Monday')").click();
        await dockTabs(page).nth(0).click();
        await open(page, "button:has-text('North depot')").click();
        await page.waitForTimeout(600);
        const at = await centreOf(page, "[data-elicitation-submit]");
        await watchPoint(page, at.x, at.y, 700);
        await pressAt(page, d, at.x, at.y);
        // Past the 400 ms arm of Q2, inside the 800 ms hold.
        await page.waitForTimeout(500);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(300);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.held).toEqual(["Answer sent"]);
        expect(seen.shift).toBeLessThan(1);
        await page.waitForTimeout(1300);
        const w = await wire(page);
        expect(w.questions).toEqual(["q".repeat(32)]);
        expect(w.approvals).toEqual([]);
        expect(w.sends).toEqual(["Plan the June run."]);
        // Rule 3: after the hold, Q2 draws in the place of the line.
        await expect(page.locator("[data-dock-held]")).toHaveCount(0);
        await expect(open(page, "[data-elicitation-submit]")).toBeVisible();
      } finally {
        await ctx.close();
      }
    });

    test(`${d.name}: a double press on Fold keeps the height, and sends nothing of the thread`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "approvals" });
        await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
        await page.locator('[data-chat-ask] [data-confirmation-card] [role="tab"]').nth(1).click();
        await expect(page.locator(THREAD_SEND)).toHaveCount(1);
        // Fold is a small icon at the right. The thread's Send row goes to its
        // height, and the point stays on Fold: before the fix, the thread
        // grew under it at once.
        const at = await lineUp(page, "[data-dock-fold]", "", true);
        expect(Math.abs(at.lined - at.y)).toBeLessThanOrEqual(2);
        await watchPoint(page, at.x, at.y, 700);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(150);
        await pressAt(page, d, at.x, at.y);
        await page.waitForTimeout(700);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.shift).toBeLessThan(1);
        await page.waitForTimeout(800);
        await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
        const w = await wire(page);
        expect(w.questions).toEqual([]);
        expect(w.approvals).toEqual([]);
        expect(w.sends).toEqual(["Plan the June run."]);
      } finally {
        await ctx.close();
      }
    });
  }
});
