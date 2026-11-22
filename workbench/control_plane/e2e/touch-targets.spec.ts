import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

/**
 * The tap size of the shared controls (task 3559). The edit card of a new
 * Projects task draws the shared `AssigneeChips`, `Button`, `Input`,
 * `SelectButton` and `Switch`, so it is the page that measures them. The page
 * is the stubbed chat harness of `task-edit-card.spec.ts`: no model and no
 * server run.
 *
 * Touch emulation (`hasTouch` and `isMobile`) makes `(pointer: coarse)` and
 * `(hover: none)` match, and the 390 px width is under the 1024 px limit of the
 * `pointer-coarse` variant in `globals.css`. A plain Playwright context matches
 * neither, so earlier captures never drew the touch size.
 *
 * Glass is the only look (D149). The classic run removes `data-look` after
 * hydration. It exists for the chip x only, whose rule does not depend on the
 * look. Classic Button sizes carry no touch rule, and no member sees that look.
 *
 * The desktop test pins the fine-pointer boxes, so the touch rule cannot leak.
 * Mutation: remove `CHIP_REMOVE_TOUCH` from the remove button, and the chip
 * tests turn red.
 */

const REQ = "a".repeat(32);
const PID = "11111111-2222-4333-8444-555555555555";
const ANN = "ann@acme.test";
const BOB = "bob@acme.test";
const CAT = "cat@acme.test";
const DESCRIPTION = "Ship the pack.\nThen call Kim.";
const VALUES = {
  due: "2026-12-01", start: "", status: "Doing", important: null, leveraged: false,
  description: DESCRIPTION, assignees: [ANN, BOB],
};
const DIRECTORY = [
  { assignee: ANN, name: "Ann Rao" },
  { assignee: BOB, name: "Bob Lee" },
  { assignee: CAT, name: "Cat Jones" },
];
const LANES = ["Todo", "Doing", "Review", "Done"];
const CONTEXT = [
  "Check each field, then approve.",
  "project: «Launch»",
  "name: «Ship the pack»",
  "due_at: 2026-12-01",
  "status: Doing",
  `assignees: ${ANN}, ${BOB}`,
].join("\n");

declare global {
  interface Window { __answers: { request_id: string; answer: string }[] }
}

async function install(page: Page, withEdit = true) {
  await page.addInitScript(({ withEdit, values, pid, req, context }) => {
    window.localStorage.clear();
    const w = window as unknown as Window;
    w.__answers = [];
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
        w.__answers.push(JSON.parse(String(init?.body ?? "{}")));
        return json({ ok: true });
      }
      if (url.includes("/api/chat/active-sessions")) return json([]);
      if (url.includes("/api/auth/me")) {
        return json({
          email: "jane@acme.test", user_id: "jane", authenticated: true, is_active: true,
          organization: { id: "00000000-0000-0000-0000-0000000000e2", slug: "e2e" },
          roles: ["owner"], legacy_role: "executive", features: ["chat", "projects"],
          features_denied: [], agents: ["orchestrator"], permissions: ["*"],
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
          start(ctl) {
            ctl.enqueue(frame({ type: "message_start", messageId: "m-1" }));
            ctl.enqueue(frame({ type: "delta", messageId: "m-1", content: "Here is the task.\n\n" }));
            ctl.enqueue(frame({ type: "tool_start", id: "t-1", name: "create_task", args: {} }));
            ctl.enqueue(frame({ type: "custom", name: "confirmation_requested", value: {
              title: "Create this task?", detail: "«Ship the pack» · in Launch · status Doing",
              context, fenced: true, request_id: req,
              ...(withEdit ? { edit: { kind: "create_task", project_id: pid, values } } : {}),
            } }));
            // The run waits on the card. The stream stays open.
          },
        });
        return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      return orig(input, init);
    };
  }, { withEdit, values: VALUES, pid: PID, req: REQ, context: CONTEXT });
  await page.route("**/api/agent/list", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/models/all", (r) => r.fulfill({ json: { models: [], source: "mock" } }));
  await page.route(/.*\/api\/integrations\/status.*/, (r) => r.fulfill({ json: [] }));
  await page.route("**/api/memory/**", (r) => r.fulfill({ json: [] }));
  // The directory: the picker's list, and the exact lookup of the names. Bob is a known address, `typo` is not.
  await page.route("**/api/projects/assignees**", (r) => {
    const q = (new URL(r.request().url()).searchParams.get("q") ?? "").toLowerCase();
    const rows = DIRECTORY.filter((p) => !q || p.name.toLowerCase().includes(q) || p.assignee.includes(q))
      .map((p) => ({ ...p, kind: "person", has_login: true, top_skills: [], warnings: [] }));
    return r.fulfill({ json: { people: rows, agents: [], hr_visible: false } });
  });
  // The lanes of the project of the card (`projectsApi.statuses`). `Doing` is the lane of the card.
  await page.route("**/api/projects/nodes/*/statuses**", (r) => r.fulfill({ json: {
    rows: LANES.map((name, position) => ({ id: `lane-${position}`, project_id: PID, name, color: "", position, category: "active" })),
    total: LANES.length, counts: {}, owner_id: PID,
  } }));
  await page.route("**/api/projects/people/names**", (r) => {
    const emails = (new URL(r.request().url()).searchParams.get("emails") ?? "").split(",").filter(Boolean);
    const names: Record<string, string> = {};
    for (const p of DIRECTORY) if (emails.includes(p.assignee)) names[p.assignee] = p.name;
    return r.fulfill({ json: { names } });
  });
}

async function openCard(page: Page, withEdit = true) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await install(page, withEdit);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) {
    await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  }
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Make a task to ship the pack.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-confirmation-card]")).toBeVisible({ timeout: 15_000 });
}


const OUT = process.env.TOUCH_OUT;
const TOUCH = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true };
const FINE = { viewport: { width: 1280, height: 800 }, hasTouch: false, isMobile: false };

type Row = { name: string; tag: string; w: number; h: number; hit: { w: number; h: number } };

/** Width, height and tap area of each control in the card. The tap area is probed with elementFromPoint, so a pseudo-element counts. */
async function measure(page: Page): Promise<Row[]> {
  return page.evaluate(() => {
    const card = document.querySelector("[data-confirmation-card]")!;
    const els = Array.from(card.querySelectorAll<HTMLElement>("button, input, textarea, [role=switch], select, a[href]"));
    const hit = (el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + r.height / 2);
      const ok = (x: number, y: number) => { const t = document.elementFromPoint(x, y); return !!t && (t === el || el.contains(t)); };
      const run = (dx: number, dy: number) => { let n = 0; while (n < 60 && ok(cx + dx * (n + 1), cy + dy * (n + 1))) n++; return n; };
      return { w: run(1, 0) + run(-1, 0) + 1, h: run(0, 1) + run(0, -1) + 1 };
    };
    return els.filter((e) => e.getBoundingClientRect().width > 0).map((el) => {
      const r = el.getBoundingClientRect();
      const label = el.getAttribute("aria-label") ?? (el.hasAttribute("data-task-edit-due") ? "due-input" : (el.textContent || el.getAttribute("placeholder") || el.tagName).trim().slice(0, 32));
      return { name: label, tag: el.tagName.toLowerCase() + (el.getAttribute("role") ? "/" + el.getAttribute("role") : ""), w: +r.width.toFixed(1), h: +r.height.toFixed(1), hit: hit(el) };
    });
  });
}

async function openCardIn(page: Page, density: string, look: "glass" | "classic") {
  await install(page);
  await page.addInitScript((d) => window.localStorage.setItem("cc-density", d), density);
  await page.goto("/chat");
  const picker = page.getByText("New session", { exact: true });
  const box = page.getByRole("textbox", { name: /^Message / });
  await expect(picker.or(box)).toBeVisible({ timeout: 60_000 });
  if (await picker.isVisible()) await page.getByRole("button", { name: /General-purpose AI company brain/i }).click();
  await expect(box).toBeEnabled({ timeout: 30_000 });
  await box.fill("Make a task to ship the pack.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator("[data-confirmation-card]")).toBeVisible({ timeout: 15_000 });
  if (look === "classic") await page.evaluate(() => document.documentElement.removeAttribute("data-look"));
  await page.waitForTimeout(800);
  const rows = await measure(page);
  if (OUT) {
    mkdirSync(OUT, { recursive: true });
    const mq = await page.evaluate(() => ({ coarse: matchMedia("(pointer: coarse)").matches, none: matchMedia("(hover: none)").matches }));
    writeFileSync(`${OUT}/m-${look}-${density}-${mq.coarse ? "touch" : "fine"}.json`, JSON.stringify({ mq, rows }, null, 1));
  }
  return rows;
}

const chips = (rows: Row[]) => rows.filter((r) => r.name.startsWith("Unassign "));

test.describe("touch targets of the shared controls (task 3559)", () => {
  test.describe.configure({ timeout: 120_000 });

  for (const look of ["glass", "classic"] as const) {
    for (const density of ["compact", "comfortable"]) {
      test(`touch, ${look}, ${density}: the chip x has a tap area of ${look === "glass" ? 32 : 24} px or more`, async ({ browser }) => {
        const ctx = await browser.newContext({ ...TOUCH, baseURL: "http://localhost:3101" });
        const page = await ctx.newPage();
        const rows = await openCardIn(page, density, look);
        expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches && matchMedia("(hover: none)").matches)).toBe(true);
        const x = chips(rows);
        expect(x).toHaveLength(2);
        for (const r of x) {
          // The drawn box stays small. The tap area is the pseudo-element.
          expect(r.w).toBeLessThanOrEqual(13);
          // Classic is a look no member sees. A neighbour covers 2 px of its tap area, so it keeps the WCAG 2.2 floor of 24 px.
          const floor = look === "glass" ? 32 : 24;
          expect(r.hit.w, r.name).toBeGreaterThanOrEqual(floor);
          expect(r.hit.h, r.name).toBeGreaterThanOrEqual(floor);
        }
        await ctx.close();
      });
    }
  }

  for (const density of ["compact", "comfortable"]) {
    test(`touch, glass, ${density}: every button, switch and field is 32 px high or more`, async ({ browser }) => {
      const ctx = await browser.newContext({ ...TOUCH, baseURL: "http://localhost:3101" });
      const page = await ctx.newPage();
      const rows = await openCardIn(page, density, "glass");
      const others = rows.filter((r) => !r.name.startsWith("Unassign ") && !r.tag.startsWith("textarea"));
      expect(others.length).toBeGreaterThanOrEqual(7);
      for (const r of others) expect(r.h, `${r.tag} ${r.name}`).toBeGreaterThanOrEqual(31);
      await ctx.close();
    });
  }

  for (const [density, chip, button] of [["compact", 11, 25.8], ["comfortable", 13, 30.2]] as const) {
    test(`fine pointer, 1280 px, ${density}: the desktop boxes do not move`, async ({ browser }) => {
      const ctx = await browser.newContext({ ...FINE, baseURL: "http://localhost:3101" });
      const page = await ctx.newPage();
      const rows = await openCardIn(page, density, "glass");
      for (const r of chips(rows)) {
        expect([r.w, r.h]).toEqual([chip, chip]);
        // No pseudo-element: the tap area is the drawn box, plus a pixel of rounding in the probe.
        expect(r.hit.w).toBeLessThanOrEqual(chip + 2);
        expect(r.hit.h).toBeLessThanOrEqual(chip + 2);
      }
      for (const name of ["Approve", "Reject", "No due date"]) {
        const r = rows.find((q) => q.name === name)!;
        expect(r.h, name).toBeCloseTo(button, 1);
      }
      const sw = rows.find((q) => q.tag === "button/switch")!;
      expect([sw.w, sw.h]).toEqual([38, 22]);
      await ctx.close();
    });
  }
});
