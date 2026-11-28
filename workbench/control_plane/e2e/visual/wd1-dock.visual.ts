import { expect, test, type Browser, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * WD-1 capture rig, the D10 cut (task 4089, `projects_ai_chat.md` §24.11). Not
 * a test: run it by name with `--project=visual`. One turn streams a new-mail
 * card and an `ask_questions` card. The run stays live, so both wait. Every
 * card draws in the thread, and the row above the box names what waits. Each
 * file it writes starts with `d10-`.
 */

const OUT = process.env.UX_OUT || "ux-shots/wd1-dock";

const FORM = {
  kind: "new",
  message_id: "",
  mailbox: "Work · jane@acme.test",
  subject: "Rates for May",
  body: [
    "Hi Kim,",
    "",
    "The rates for May are attached. Three changes from April:",
    "1. The base rate moves to 4.2 per unit.",
    "2. The rush fee drops for orders over 500 units.",
    "3. Freight to the north depot is now included.",
    "",
    "Please confirm by Friday, so we can book the June run.",
    "",
    "Best,",
    "Jane",
  ].join("\n"),
  recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["kim@contoso.test"], cc: [] } },
  files: [],
  notices: [],
  note: "",
};

async function install(page: Page, opts: { dock: boolean; light?: boolean; keyboardUp?: number; twoQuestions?: boolean; noQuestion?: boolean }) {
  await page.addInitScript(({ dock, light, form, keyboardUp, twoQuestions, noQuestion }) => {
    window.localStorage.clear();
    if (light) window.localStorage.setItem("theme", "light");
    // The shell read (and so the flag) runs only with the shell nav on.
    window.localStorage.setItem("cc-shell-nav", "1");
    if (keyboardUp) {
      // `keyboardUp` is the layout height. The browser viewport is the part
      // above the soft keyboard, as on a phone with the keyboard up.
      const fake = new EventTarget() as EventTarget & Record<string, unknown>;
      const realHeight = Object.getOwnPropertyDescriptor(window, "innerHeight")?.get?.bind(window);
      const visible = () => (realHeight ? realHeight() : document.documentElement.clientHeight);
      Object.defineProperty(window, "innerHeight", { get: () => keyboardUp });
      Object.defineProperty(fake, "height", { get: () => visible() });
      Object.defineProperty(fake, "width", { get: () => window.innerWidth });
      Object.defineProperty(fake, "offsetTop", { get: () => 0 });
      Object.defineProperty(fake, "offsetLeft", { get: () => 0 });
      Object.defineProperty(fake, "scale", { get: () => 1 });
      Object.defineProperty(window, "visualViewport", { get: () => fake });
    }
    const enc = new TextEncoder();
    const frame = (e: unknown) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const orig = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/auth/me/shell")) {
        return json({ answered: "answered", preset: null, pins: null, known: null, waiting_dock: dock });
      }
      if (url.includes("/api/agent/respond-input")) return json({ ok: true });
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
      if (url.includes("/api/agent/cancel")) return json({ cancelled: true });
      if (url.includes("/api/agent/chat")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.reconnect) return json({}, 404);
        if (body.steerFollowUp) return json({ steered: false, followUp: false, reason: "nothing_pending" }, 202);
        const stream = new ReadableStream<Uint8Array>({
          start(ctl) {
            const send = (e: unknown) => ctl.enqueue(frame(e));
            send({ type: "message_start", messageId: "m-1" });
            const plan = Array.from({ length: 12 }, (_, i) => `${i + 1}. A step of the plan for the June run.`).join("\n");
            send({ type: "delta", messageId: "m-1", content: `I drafted the email to Kim and a task for the June run. One question first.\n\n${plan}` });
            send({ type: "custom", name: "confirmation_requested", value: {
              title: "Send this new email?", detail: "From Work · jane@acme.test · Rates for May",
              request_id: "a".repeat(32), reply: form,
            } });
            if (!noQuestion) send({ type: "custom", name: "elicitation_requested", value: {
              request_id: "q".repeat(32),
              questions: [{
                header: "Depot", question: "Which depot should the June run ship from?", multiSelect: false,
                allowFreeformInput: true,
                options: [{ label: "North depot", recommended: true }, { label: "South depot" }],
              }],
            } });
            if (twoQuestions) {
              send({ type: "custom", name: "user_input_requested", value: {
                request_id: "u".repeat(32), question: "Which day should the run start?", choices: ["Monday", "Tuesday"], allowFreeform: false,
              } });
            }
            // The run stays live: every card waits.
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { dock: opts.dock, light: !!opts.light, form: FORM, keyboardUp: opts.keyboardUp ?? 0, twoQuestions: !!opts.twoQuestions, noQuestion: !!opts.noQuestion });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
}

async function openTurn(page: Page, dock: boolean) {
  await page.goto("/chat");
  const box = page.getByRole("textbox", { name: /^Message / });
  const picker = page.getByRole("button", { name: /General-purpose/i }).first();
  await expect(picker.or(box).first()).toBeVisible({ timeout: 90_000 });
  if (await picker.isVisible().catch(() => false)) await picker.click();
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Write to Kim about the May rates, and plan the June run.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const waits = dock ? page.locator("[data-dock-row]") : page.locator("[data-confirmation-card]");
  await expect(waits.first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(1200);
}

/** Scroll the thread (the box just before the composer form) to its top, so each card is out of view. */
async function threadToTop(page: Page) {
  await page.evaluate(() => {
    (document.querySelector("[data-chat-composer]")!.closest("form")!.previousElementSibling as HTMLElement).scrollTop = 0;
  });
  await page.waitForTimeout(300);
}

/** Before each capture of the row: its words, its one button, and its place above the box. */
async function check(page: Page) {
  const r = await page.evaluate(() => {
    const row = document.querySelector<HTMLElement>("[data-dock-row]");
    const box = document.querySelector<HTMLElement>("[data-chat-composer]");
    const b = row?.getBoundingClientRect();
    return {
      words: (row?.innerText ?? "").replace(/\s+/g, " ").trim(),
      buttons: row?.querySelectorAll("button").length ?? 0,
      cards: row?.querySelectorAll("[data-confirmation-card], [data-elicitation-card]").length ?? 0,
      height: b?.height ?? 0,
      bottom: b?.bottom ?? 0,
      boxTop: box?.getBoundingClientRect().top ?? 0,
      focus: document.activeElement?.tagName ?? "",
    };
  });
  expect(r.words, "the row shows").not.toBe("");
  expect(r.cards).toBe(0);
  expect(r.bottom).toBeLessThanOrEqual(r.boxTop);
  return `row: ${r.words} | ${r.buttons} button, ${r.cards} cards, ${Math.round(r.height)} px tall | focus: ${r.focus || "none"}`;
}

/** After Review: the approval card shows whole above the row, or its top. */
async function checkReview(page: Page) {
  const r = await page.evaluate(() => {
    const t = document.querySelector("[data-chat-composer]")!.closest("form")!.previousElementSibling as HTMLElement;
    const card = document.querySelector<HTMLElement>("[data-chat-ask] [data-confirmation-card]")!;
    const portTop = t.getBoundingClientRect().top + t.clientTop;
    const c = card.getBoundingClientRect();
    return {
      cardTop: c.top, cardBottom: c.bottom, portTop, portBottom: portTop + t.clientHeight,
      rowTop: document.querySelector("[data-dock-row]")!.getBoundingClientRect().top,
      heading: document.activeElement?.hasAttribute("data-rollup-title") ?? false,
      box: document.activeElement?.tagName === "TEXTAREA",
      title: card.querySelector("[data-rollup-title]")?.textContent?.trim() ?? "",
    };
  });
  const tall = r.cardBottom - r.cardTop > r.portBottom - r.portTop;
  if (tall) expect(Math.abs(r.cardTop - r.portTop)).toBeLessThanOrEqual(2);
  else {
    expect(r.cardTop).toBeGreaterThanOrEqual(r.portTop - 1);
    expect(r.cardBottom).toBeLessThanOrEqual(Math.min(r.portBottom, r.rowTop) + 1);
  }
  const focus = r.box ? "the box keeps the focus" : r.heading ? `focus on the heading "${r.title}"` : "focus elsewhere";
  return `after Review: ${focus}${tall ? ", the card is taller than the room: its top shows" : ", the card shows whole above the row"}`;
}

function note(name: string, line: string) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.appendFileSync(path.join(OUT, "primary.txt"), `${name}: ${line}\n`);
}

async function shot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

async function phone(browser: Browser, opts: { dock: boolean; light?: boolean; keyboardUp?: number; height?: number }) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: opts.height ?? 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2,
    baseURL: "http://localhost:3101",
  });
  const page = await ctx.newPage();
  await install(page, opts);
  await openTurn(page, opts.dock);
  return { page, ctx };
}

const blur = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

test.describe.configure({ mode: "serial" });
test.setTimeout(300_000);

test.beforeAll(() => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(
    path.join(OUT, "primary.txt"),
    "The D10 cut (2026-11-28). Each line: the capture, then the row's words and button, or the state after Review. The dock holds no card and no commit control.\n",
  );
});

for (const light of [false, true]) {
  const mode = light ? "light" : "dark";
  test(`desktop 1280 ${mode}: one question and one approval wait, then Review`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await install(page, { dock: true, light });
    await openTurn(page, true);
    await threadToTop(page);
    await blur(page);
    note(`d10-row-1280-${mode}`, await check(page));
    await shot(page, `d10-row-1280-${mode}`);
    await page.locator("[data-dock-review]").click();
    await page.waitForTimeout(600);
    note(`d10-review-1280-${mode}`, await checkReview(page));
    await shot(page, `d10-review-1280-${mode}`);
  });
}

test("phone 390: one question and one approval wait, then Review", async ({ browser }) => {
  const { page, ctx } = await phone(browser, { dock: true });
  await threadToTop(page);
  await blur(page);
  note("d10-row-390", await check(page));
  await shot(page, "d10-row-390");
  await page.locator("[data-dock-review]").tap();
  await page.waitForTimeout(600);
  note("d10-review-390", await checkReview(page));
  await shot(page, "d10-review-390");
  await ctx.close();
});

test("phone 390: keyboard up, the row stays one line, and Review only scrolls", async ({ browser }) => {
  // 844 tall with a 330 px keyboard: 514 px show above it.
  const { page, ctx } = await phone(browser, { dock: true, keyboardUp: 844, height: 514 });
  await page.getByRole("textbox", { name: /^Message / }).focus();
  await threadToTop(page);
  note("d10-phone-390-keyboard-up", await check(page));
  await shot(page, "d10-phone-390-keyboard-up");
  await page.locator("[data-dock-review]").tap();
  await page.waitForTimeout(600);
  note("d10-phone-390-keyboard-up-review", await checkReview(page));
  await shot(page, "d10-phone-390-keyboard-up-review");
  await ctx.close();
});

test("desktop 1280: the last item leaves, and \"Nothing waits\" holds the place", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await install(page, { dock: true, light: true });
  await openTurn(page, true);
  // A pointer held down keeps the inert row for the capture (D10 rule 5).
  await page.mouse.move(640, 120);
  await page.mouse.down();
  await page.evaluate(() => {
    document.querySelectorAll<HTMLElement>("[data-chat-ask] [data-not-now-close], [data-chat-ask] [data-reply-discard]").forEach((b) => b.click());
  });
  await expect(page.locator('[data-dock-row="empty"]')).toBeVisible({ timeout: 5000 });
  note("d10-nothing-waits-1280-light", `row: ${(await page.locator("[data-dock-row]").innerText()).trim()} | ${await page.locator("[data-dock-row] button").count()} buttons`);
  await shot(page, "d10-nothing-waits-1280-light");
  await page.mouse.up();
});

test("flag off, for comparison: 1280 and 390", async ({ page, browser }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await install(page, { dock: false });
  await openTurn(page, false);
  note("d10-flag-off-1280", "no row: every card in the thread, and the bar of main");
  await shot(page, "d10-flag-off-1280");
  const { page: p, ctx } = await phone(browser, { dock: false });
  note("d10-flag-off-390", "no row: every card in the thread, and the bar of main");
  await shot(p, "d10-flag-off-390");
  await ctx.close();
});
