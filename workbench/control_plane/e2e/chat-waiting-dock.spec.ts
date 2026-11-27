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
  }
}

type Cards = "all" | "one";

async function install(page: Page, opts: { cards: Cards; gated?: boolean }) {
  await page.addInitScript(({ cards, gated, form, values, pid, mail, task, ask }) => {
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
            send({ type: "delta", messageId: "m-1", content: "Here is what I need from you." });
            if (gated) await released;
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
  }, { cards: opts.cards, gated: !!opts.gated, form: FORM, values: VALUES, pid: PID, mail: MAIL, task: TASK, ask: ASK });
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

async function start(page: Page, opts: { cards: Cards; gated?: boolean }) {
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

test.describe("the waiting dock (WD-1)", () => {
  test.describe.configure({ timeout: 180_000 });

  test("the dock is outside the form: Enter in a card field and an option tap send nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toBeVisible({ timeout: 20_000 });
    expect(await dock(page).evaluate((el) => !!el.closest("form"))).toBe(false);

    // The new-mail card: Enter in its Subject.
    await page.getByRole("textbox", { name: "Subject" }).press("Enter");
    // The task card: Enter in its due date.
    await page.locator('[role="tab"]').nth(1).click();
    await page.locator("[data-task-edit-due]").press("Enter");
    // The question: Enter in its own answer field.
    await page.locator('[role="tab"]').nth(2).click();
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

  test("the fold row opens the card and approves nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await start(page, { cards: "all" });
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open", { timeout: 20_000 });
    await page.locator("[data-dock-fold]").click();
    await expect(dock(page)).toHaveAttribute("data-waiting-dock", "folded");
    const review = page.locator("[data-dock-unfold]");
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
    const approve = page.locator("[data-confirmation-approve]");
    await page.waitForTimeout(600); // the card arms after 400 ms
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
      await page.locator('[role="tab"]').nth(2).tap();
      const own = page.getByRole("textbox", { name: "Your own answer" });
      await own.tap();
      await page.evaluate(() => window.__kb(400));
      await expect(dock(page)).toHaveAttribute("data-waiting-dock", "open");
      await own.pressSequentially("via the ring road");
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
});
