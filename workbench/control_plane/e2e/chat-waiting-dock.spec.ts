import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * The row of the waiting dock in a real browser (WD-1, `projects_ai_chat.md`
 * §24.11, D10, 2026-11-28).
 *
 * D10: the dock holds no card and no commit control. With the flag on, every
 * card draws in the thread as with the flag off, and ONE row above the box
 * names what waits: "Waits for you: <label> and N more", and Review. Review
 * only scrolls. The row never takes the focus, never blurs the box, and never
 * changes what Enter in the box does.
 *
 * No gateway runs. The page's `fetch` is stubbed: the shell read says
 * `waiting_dock: true` (or false), and the chat stream holds the cards of
 * each scenario and stays live. `window.__wire` records EVERY request that is
 * not a GET. `window.__push(e)` sends one more stream event, so a test can
 * answer a card from elsewhere or end the run. `window.__kb(px)` raises a
 * fake soft keyboard of `px` pixels.
 *
 * Each row asserts that nothing was sent, answered, rejected or saved that
 * the member did not press on a card in the thread. Where a press can land
 * on the thread, the row compares the whole wire with the same script with
 * the flag off.
 */

const PID = "11111111-1111-1111-1111-111111111111";
const MAIL = "a".repeat(32);
const TASK = "c".repeat(32);
const TASK2 = "d".repeat(32);
const ASK = "q".repeat(32);
const PICK = "p".repeat(32);
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

interface Wire { path: string; body: string }

declare global {
  interface Window {
    __wire: Wire[];
    __release: () => void;
    __kb: (px: number) => void;
    __push: (e: unknown) => void;
  }
}

/**
 * The cards of a scenario. The run stays live, so each card waits.
 * - `approvals`: a task, a new mail and a task, then an `ask_questions` card;
 * - `one`: one task approval;
 * - `longMail`: one long new mail;
 * - `genui`: one generative-UI ask with a request id.
 */
type Cards = "approvals" | "one" | "longMail" | "genui";

async function install(page: Page, opts: { cards: Cards; gated?: boolean; dock: boolean }) {
  await page.addInitScript(({ cards, gated, dock, form, longForm, values, pid, mail, task, task2, ask, pick }) => {
    window.localStorage.clear();
    window.localStorage.setItem("cc-shell-nav", "1");
    const w = window as unknown as Window;
    w.__wire = [];
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
    w.__push = (e: unknown) => {
      send(e);
      if (e && typeof e === "object" && (e as { type?: string }).type === "done") {
        try { live?.close(); } catch { /* closed */ }
        live = null;
      }
    };
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (method !== "GET") {
        w.__wire.push({ path: new URL(url, location.href).pathname, body: typeof init?.body === "string" ? init.body : "" });
      }
      if (url.includes("/api/auth/me/shell")) {
        return json({ answered: "answered", preset: null, pins: null, known: null, waiting_dock: dock });
      }
      if (url.includes("/api/agent/respond-input")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
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
        if (live) return json({ steered: true, delivered: true, pendingReplay: false }, 202);
        const stream = new ReadableStream<Uint8Array>({
          async start(ctl) {
            live = ctl;
            send({ type: "message_start", messageId: "m-1" });
            // A long answer, so the thread scrolls.
            const lines = Array.from({ length: 40 }, (_, i) => `Step ${i + 1} of the plan for the June run.`).join("\n\n");
            send({ type: "delta", messageId: "m-1", content: `Here is what I need from you.\n\n${lines}` });
            if (gated) await released;
            if (cards === "genui") {
              send({ type: "custom", name: "generative_ui", segmentCutoff: 0, value: {
                request_id: pick,
                root: { type: "template", props: { name: "optionPicker", data: {
                  title: "Which tags should I register?", multi: true,
                  options: [{ id: "ux", label: "UX" }, { id: "bug", label: "Bug" }],
                } } },
              } });
              return;
            }
            const task1 = { type: "custom", name: "confirmation_requested", value: {
              title: "Create this task?", detail: "«Ship the pack» · Launch", request_id: task,
            } };
            if (cards === "one") { send(task1); return; }
            if (cards === "longMail") {
              send({ type: "custom", name: "confirmation_requested", value: {
                title: "Send this new email?", detail: "From Work · Rates for May", request_id: mail, reply: longForm,
              } });
              return;
            }
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
            send({ type: "custom", name: "elicitation_requested", value: {
              request_id: ask,
              questions: [{
                header: "Depot", question: "Which depot should the June run ship from?",
                multiSelect: false, allowFreeformInput: true,
                options: [{ label: "North depot" }, { label: "South depot" }],
              }],
            } });
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, {
    cards: opts.cards, gated: !!opts.gated, dock: opts.dock, form: FORM, longForm: LONG_FORM, values: VALUES,
    pid: PID, mail: MAIL, task: TASK, task2: TASK2, ask: ASK, pick: PICK,
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

/** The saves of the thread itself are no act of the member. */
const BACKGROUND = /^\/api\/chat\/sessions(\/|$)/;

/** Every request that is not a GET and not a save of the thread. */
const acts = async (page: Page) =>
  (await page.evaluate(() => window.__wire)).filter((x) => !BACKGROUND.test(x.path));

/** The ids and times of each request change from run to run. The rest is the act. */
const VOLATILE = new Set(["threadId", "thread_id", "assistantMessageId", "userMessageId", "userMessageTs", "sessionId"]);
function norm(x: Wire) {
  try {
    const b = JSON.parse(x.body) as Record<string, unknown>;
    return { path: x.path, body: Object.fromEntries(Object.entries(b).filter(([k]) => !VOLATILE.has(k))) };
  } catch {
    return { path: x.path, body: x.body };
  }
}

/** The one act of `start`: the first chat message. */
async function onlyFirstSend(page: Page) {
  const a = await acts(page);
  expect(a.map((x) => x.path)).toEqual(["/api/agent/chat"]);
  expect(JSON.parse(a[0].body).message).toBe("Plan the June run.");
}

const box = (page: Page) => page.getByRole("textbox", { name: /^Message / });
const row = (page: Page) => page.locator("[data-dock-row]");
const review = (page: Page) => page.locator("[data-dock-review]");
const threadCard = (page: Page) => page.locator("[data-chat-ask] [data-confirmation-card]");

async function start(page: Page, opts: { cards: Cards; gated?: boolean; dock: boolean }) {
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

/** The device of a row: the viewport, and how the member presses. */
const DEVICES = [
  { name: "390 touch, keyboard up", width: 390, height: 844, touch: true, kb: 320, press: "tap" },
  { name: "390 touch, keyboard down", width: 390, height: 844, touch: true, kb: 0, press: "tap" },
  { name: "1280 mouse", width: 1280, height: 800, touch: false, kb: 0, press: "mouse" },
  { name: "1280 keyboard", width: 1280, height: 800, touch: false, kb: 0, press: "key" },
] as const;
type Device = (typeof DEVICES)[number];
type Screen = Pick<Device, "width" | "height" | "touch">;

async function device(browser: Browser, d: Screen) {
  const ctx = await browser.newContext({
    viewport: { width: d.width, height: d.height }, hasTouch: d.touch, isMobile: d.touch && d.width < 600,
    baseURL: "http://localhost:3101",
  });
  return { ctx, page: await ctx.newPage() };
}

async function threadTo(page: Page, where: "foot" | "top") {
  await page.evaluate((w) => {
    const t = document.querySelector("[data-chat-composer]")!.closest("form")!.previousElementSibling as HTMLElement;
    t.scrollTop = w === "foot" ? t.scrollHeight : 0;
  }, where);
  await page.waitForTimeout(150);
}

/** Where a card is, against the thread's room and the row's top. */
async function place(page: Page, sel: string) {
  return page.evaluate((s) => {
    const t = document.querySelector("[data-chat-composer]")!.closest("form")!.previousElementSibling as HTMLElement;
    const card = document.querySelector<HTMLElement>(s)!;
    const tr = t.getBoundingClientRect();
    const portTop = tr.top + t.clientTop;
    const c = card.getBoundingClientRect();
    const r = document.querySelector("[data-dock-row]")?.getBoundingClientRect();
    const at = document.activeElement;
    return {
      cardTop: c.top, cardBottom: c.bottom, cardH: c.height,
      portTop, portBottom: portTop + t.clientHeight, portH: t.clientHeight,
      rowTop: r?.top ?? null,
      focusInCard: !!at && card.contains(at) && at.tagName !== "BUTTON",
      focusIsHead: !!at && card.contains(at) && (at.hasAttribute("data-rollup-title") || at === card),
      focusIsBox: at?.tagName === "TEXTAREA",
    };
  }, sel);
}

/**
 * Watch a point, each frame, for `ms`. It records each control that shows
 * there and is not the first one, and how far the row's top moved. Each part
 * of the row, the inert "Nothing waits" too, counts as the row.
 */
async function watchPoint(page: Page, x: number, y: number, ms: number) {
  await page.evaluate(([px, py, span]) => {
    const w = window as unknown as { __watch: { moved: string[]; shift: number } };
    const CONTROL = "button, a[href], input, textarea, select, [role='tab']";
    const at0 = document.elementFromPoint(px, py);
    const first = at0?.closest(CONTROL) ?? at0;
    const top0 = document.querySelector<HTMLElement>("[data-dock-row]")?.getBoundingClientRect().top ?? null;
    const until = performance.now() + span;
    w.__watch = { moved: [], shift: 0 };
    const tick = () => {
      if (performance.now() > until) return;
      const el = document.elementFromPoint(px, py);
      const same = !!el && (el.closest(CONTROL) ?? el) === first;
      if (el && !same && !el.closest("[data-dock-row]")) {
        const d = (el.closest(CONTROL) ?? el).outerHTML.slice(0, 90);
        if (!w.__watch.moved.includes(d)) w.__watch.moved.push(d);
      }
      const r = document.querySelector<HTMLElement>("[data-dock-row]");
      if (r && top0 !== null) w.__watch.shift = Math.max(w.__watch.shift, Math.abs(r.getBoundingClientRect().top - top0));
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [x, y, ms] as const);
}

const watched = (page: Page) =>
  page.evaluate(() => (window as unknown as { __watch: { moved: string[]; shift: number } }).__watch);

async function tapAt(page: Page, touch: boolean, x: number, y: number) {
  if (touch) await page.touchscreen.tap(x, y);
  else await page.mouse.click(x, y);
}

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

/** Set the focus a row starts from: the box (the keyboard up), or nowhere. */
async function startFocus(page: Page, d: Device) {
  if (d.kb > 0) {
    await box(page).focus();
    await page.evaluate((px) => window.__kb(px), d.kb);
  } else {
    await blur(page);
  }
}

/** One press on Review, as the device presses. */
async function pressReview(page: Page, d: Device, at: { x: number; y: number }) {
  if (d.press === "key") await page.keyboard.press("Enter");
  else await tapAt(page, d.press === "tap", at.x, at.y);
}

const resolve = (page: Page, id: string) =>
  page.evaluate((rid) => window.__push({ type: "custom", name: "confirmation_resolved", value: { request_id: rid, answer: "APPROVE" } }), id);

test.describe("the row of the waiting dock (WD-1, D10)", () => {
  test.describe.configure({ timeout: 180_000 });

  // ── Review: a double press and a late press commit nothing ─────────────

  for (const d of DEVICES) {
    for (const where of ["foot", "top"] as const) {
      test(`${d.name}, thread at its ${where}: double and late presses on Review commit nothing and keep the row still`, async ({ browser }) => {
        const { ctx, page } = await device(browser, d);
        try {
          await start(page, { cards: "approvals", dock: true });
          await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
          await expect(review(page)).toBeVisible();
          // Fold no longer exists, and the row holds no card.
          await expect(page.locator("[data-dock-fold], [data-dock-fold-row], [data-dock-body], [data-dock-host]")).toHaveCount(0);
          await expect(row(page).locator("button")).toHaveCount(1);
          await expect(row(page).locator("[data-confirmation-card], [data-elicitation-card]")).toHaveCount(0);
          await threadTo(page, where);
          await startFocus(page, d);
          if (d.press === "key") await review(page).focus();
          const b = (await review(page).boundingBox())!;
          const at = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
          const top0 = (await row(page).boundingBox())!.y;
          await watchPoint(page, at.x, at.y, 6000);
          for (const gap of [150, 500, 900]) {
            await pressReview(page, d, at);
            await page.waitForTimeout(gap);
            await pressReview(page, d, at);
            await page.waitForTimeout(300);
          }
          const seen = await watched(page);
          expect(seen.moved).toEqual([]);
          expect(seen.shift).toBeLessThan(1);
          expect(Math.abs((await row(page).boundingBox())!.y - top0)).toBeLessThan(1);
          // Nothing went: the first message only.
          await page.waitForTimeout(500);
          await onlyFirstSend(page);
          // The card shows whole above the row, or its top.
          const p = await place(page, "[data-chat-ask] [data-confirmation-card]");
          if (p.cardH > p.portH) expect(Math.abs(p.cardTop - p.portTop)).toBeLessThanOrEqual(4);
          else {
            expect(p.cardTop).toBeGreaterThanOrEqual(p.portTop - 1);
            expect(p.cardBottom).toBeLessThanOrEqual(p.portBottom + 1);
            expect(p.cardBottom).toBeLessThanOrEqual(p.rowTop! + 1);
          }
          // The focus: the box keeps it with the keyboard up. Else the
          // card's heading takes it, never a button.
          if (d.kb > 0) expect(p.focusIsBox).toBe(true);
          else expect(p.focusIsHead).toBe(true);
        } finally {
          await ctx.close();
        }
      });
    }
  }

  // ── Enter in the box: the flag changes nothing ─────────────────────────

  /** One script of key presses in the box: an arrival, a leave, a run end. */
  async function enterScript(browser: Browser, d: Screen, dock: boolean) {
    const { ctx, page } = await device(browser, d);
    try {
      await start(page, { cards: "approvals", gated: true, dock });
      await box(page).focus();
      await page.keyboard.type("first words");
      // An item arrives, and Enter goes at once.
      await page.evaluate(() => window.__release());
      await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
      await page.keyboard.press("Enter");
      await page.waitForTimeout(400);
      // An item leaves (an answer from elsewhere), and Enter goes at once.
      await box(page).focus();
      await page.keyboard.type(" second");
      await resolve(page, TASK);
      await page.waitForTimeout(50);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(400);
      // The run ends, and a held Enter goes.
      await box(page).focus();
      await page.keyboard.type(" third");
      await page.evaluate(() => window.__push({ type: "done" }));
      await page.waitForTimeout(50);
      for (let i = 0; i < 6; i++) {
        await page.keyboard.down("Enter");
        await page.waitForTimeout(40);
      }
      await page.keyboard.up("Enter");
      await page.waitForTimeout(1500);
      const a = await acts(page);
      // A typed Enter may answer the one waiting question, as on main
      // (§24.10 C6). The test compares the whole wire with the flag off.
      return {
        acts: a.map(norm),
        box: await box(page).inputValue(),
      };
    } finally {
      await ctx.close();
    }
  }

  for (const d of [DEVICES[0], DEVICES[3]]) {
    test(`${d.name}: Enter in the box after an arrival, a leave and a run end does with the flag on what it does with the flag off`, async ({ browser }) => {
      const on = await enterScript(browser, d, true);
      const off = await enterScript(browser, d, false);
      expect(on).toEqual(off);
      expect(on.acts.length).toBeGreaterThan(0);
    });
  }

  // ── A remote answer, the hold, and the place after the collapse ────────

  for (const d of [DEVICES[1], DEVICES[2]]) {
    test(`${d.name}: an answer from elsewhere leaves "Nothing waits" in place, and a double tap there sends nothing`, async ({ browser }) => {
      const { ctx, page } = await device(browser, d);
      try {
        await start(page, { cards: "one", dock: true });
        await expect(review(page)).toBeVisible({ timeout: 20_000 });
        await blur(page);
        const r0 = (await row(page).boundingBox())!;
        const at = { x: r0.x + r0.width / 2, y: r0.y + r0.height / 2 };
        await watchPoint(page, at.x, at.y, 1500);
        const t0 = Date.now();
        await resolve(page, TASK);
        await expect(row(page)).toHaveAttribute("data-dock-row", "empty");
        const r1 = (await row(page).boundingBox())!;
        // A double tap that straddles the 800 ms mark of the leave: each
        // press starts the wait again, so both land on the inert row. The
        // checks that only read come after the taps, so they cost no time.
        await page.waitForTimeout(Math.max(0, 650 - (Date.now() - t0)));
        await tapAt(page, d.touch, at.x, at.y);
        await page.waitForTimeout(150);
        await tapAt(page, d.touch, at.x, at.y);
        await page.waitForTimeout(400);
        await expect(row(page)).toHaveAttribute("data-dock-row", "empty");
        expect(Math.abs(r1.y - r0.y)).toBeLessThan(1);
        expect(Math.abs(r1.height - r0.height)).toBeLessThan(1);
        await expect(row(page)).toHaveText("Nothing waits");
        await expect(row(page).locator("button")).toHaveCount(0);
        const seen = await watched(page);
        expect(seen.moved).toEqual([]);
        expect(seen.shift).toBeLessThan(1);
        // 800 ms after the last release, it collapses.
        await expect(row(page)).toHaveCount(0, { timeout: 2000 });
        await onlyFirstSend(page);
      } finally {
        await ctx.close();
      }
    });
  }

  /** The run ends, the row collapses, then a double tap where it was. */
  async function afterCollapse(browser: Browser, d: Screen, dock: boolean, at: { x: number; y: number } | null) {
    const { ctx, page } = await device(browser, d);
    try {
      await start(page, { cards: "one", dock });
      await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
      let point = at;
      if (dock) {
        await expect(review(page)).toBeVisible();
        const r = (await row(page).boundingBox())!;
        point = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }
      await page.evaluate(() => window.__push({ type: "done" }));
      await expect(threadCard(page)).toHaveCount(0, { timeout: 10_000 });
      await page.waitForTimeout(2000);
      await expect(row(page)).toHaveCount(0);
      const p = point!;
      const under = await page.evaluate(([x, y]) => {
        const el = document.elementFromPoint(x, y);
        return (el?.closest("button, a[href], textarea") ?? el)?.outerHTML.slice(0, 120) ?? "";
      }, [p.x, p.y] as const);
      await watchPoint(page, p.x, p.y, 600);
      await tapAt(page, d.touch, p.x, p.y);
      await page.waitForTimeout(150);
      await tapAt(page, d.touch, p.x, p.y);
      await page.waitForTimeout(1000);
      const seen = await watched(page);
      const a = await acts(page);
      return { point: p, under, moved: seen.moved, acts: a.map(norm) };
    } finally {
      await ctx.close();
    }
  }

  for (const d of [DEVICES[1], DEVICES[2]]) {
    test(`${d.name}: after the collapse, a double tap where the row was does what it does with the flag off`, async ({ browser }) => {
      const on = await afterCollapse(browser, d, true, null);
      const off = await afterCollapse(browser, d, false, on.point);
      // The same element under the point, still for both taps, and the same wire.
      expect(on.moved).toEqual([]);
      expect(on.under).toBe(off.under);
      expect(on.acts).toEqual(off.acts);
    });
  }

  // ── The arrival waits for the pointer ──────────────────────────────────

  test("1280 mouse: an arrival takes the row's height only after the pointer comes up, and the new card shows above it", async ({ browser }) => {
    const { ctx, page } = await device(browser, DEVICES[2]);
    try {
      await start(page, { cards: "one", gated: true, dock: true });
      await threadTo(page, "foot");
      const text = (await page.getByText("Step 20 of the plan").first().boundingBox())!;
      await page.mouse.move(text.x + 4, text.y + text.height / 2);
      await page.mouse.down();
      await page.evaluate(() => window.__release());
      await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
      await page.waitForTimeout(600);
      await expect(row(page)).toHaveCount(0);
      await page.mouse.up();
      await expect(review(page)).toBeVisible({ timeout: 2000 });
      await page.waitForTimeout(300);
      const p = await place(page, "[data-chat-ask] [data-confirmation-card]");
      expect(p.cardBottom).toBeLessThanOrEqual(p.rowTop! + 1);
      await onlyFirstSend(page);
    } finally {
      await ctx.close();
    }
  });

  test("1280 keyboard: with no pointer down, an arrival takes the row at once", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "one", gated: true, dock: true });
    await page.evaluate(() => window.__release());
    await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
    await expect(review(page)).toBeVisible({ timeout: 500 });
  });

  test("1280 keyboard: Review in focus when its item leaves, then a held Enter, sends nothing", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "one", dock: true });
    await expect(review(page)).toBeVisible({ timeout: 20_000 });
    await box(page).fill("my unsent words");
    await review(page).focus();
    // The item leaves from elsewhere: Review goes, and "Nothing waits" holds the place.
    await resolve(page, TASK);
    await expect(row(page)).toHaveAttribute("data-dock-row", "empty");
    for (let i = 0; i < 8; i++) {
      await page.keyboard.down("Enter");
      await page.waitForTimeout(60);
    }
    await page.keyboard.up("Enter");
    await page.waitForTimeout(1500);
    // The box never took the focus back, so the Enter sent nothing.
    expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("TEXTAREA");
    await expect(box(page)).toHaveValue("my unsent words");
    await onlyFirstSend(page);
  });

  // ── What the row names ─────────────────────────────────────────────────

  test("the row names the open tab of the queue, and 'and N more' counts the approvals and the question", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "approvals", dock: true });
    await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
    // The question draws in the thread, as with the flag off.
    await expect(page.locator("[data-chat-ask] [data-elicitation-card]")).toHaveCount(1);
    const tabs = page.locator('[data-chat-ask] [data-confirmation-card] [role="tab"]');
    await expect(tabs).toHaveCount(3);
    await expect(row(page)).toContainText("Waits for you:");
    // 3 approvals and 1 question: the open one, and 3 more.
    await expect(row(page)).toContainText("and 3 more");
    await expect(page.locator("[data-dock-label]")).toContainText((await tabs.nth(0).innerText()).trim());
    await tabs.nth(1).click();
    await expect(page.locator("[data-dock-label]")).toContainText((await tabs.nth(1).innerText()).trim());
    // The bar draws no second pointer.
    await threadTo(page, "top");
    await page.waitForTimeout(500);
    await expect(page.locator("[data-ask-pin]")).toHaveCount(0);
    await onlyFirstSend(page);
  });

  test("a generative-UI ask counts, and Review scrolls to it", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "genui", dock: true });
    await expect(row(page)).toContainText("Which tags should I register?", { timeout: 20_000 });
    await expect(row(page)).not.toContainText("more");
    await threadTo(page, "top");
    await blur(page);
    await review(page).click();
    await page.waitForTimeout(400);
    const p = await place(page, `[data-chat-ask="genui:${PICK}"]`);
    expect(p.cardTop).toBeGreaterThanOrEqual(p.portTop - 1);
    expect(p.cardBottom).toBeLessThanOrEqual(p.rowTop! + 1);
    expect(p.focusInCard).toBe(true);
    await onlyFirstSend(page);
  });

  for (const size of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    test(`a card taller than the room shows its TOP after Review, at ${size.width}`, async ({ page }) => {
      await page.setViewportSize(size);
      await start(page, { cards: "longMail", dock: true });
      await expect(review(page)).toBeVisible({ timeout: 20_000 });
      for (const where of ["top", "foot"] as const) {
        await threadTo(page, where);
        await blur(page);
        await review(page).click();
        await page.waitForTimeout(500);
        const p = await place(page, "[data-chat-ask] [data-confirmation-card]");
        expect(p.cardH).toBeGreaterThan(p.portH);
        expect(Math.abs(p.cardTop - p.portTop)).toBeLessThanOrEqual(4);
        expect(p.focusIsHead).toBe(true);
      }
      await onlyFirstSend(page);
    });
  }

  test("flag off: no row, the bar of main, and every card in the thread", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await start(page, { cards: "approvals", dock: false });
    await expect(threadCard(page)).toHaveCount(1, { timeout: 20_000 });
    await expect(page.locator("[data-chat-ask] [data-elicitation-card]")).toHaveCount(1);
    await expect(row(page)).toHaveCount(0);
    await threadTo(page, "top");
    await expect(page.locator("[data-ask-pin]")).toBeVisible({ timeout: 3000 });
    await expect(row(page)).toHaveCount(0);
  });
});
