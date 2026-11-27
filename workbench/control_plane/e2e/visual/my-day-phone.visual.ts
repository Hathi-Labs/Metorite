import fs from "node:fs";
import path from "node:path";

import { test, type Page, type Route } from "@playwright/test";

/**
 * A throwaway rig: My Day on a touch phone (task 1898). Not committed.
 *
 *   npx playwright test my-day-phone.visual.ts --config e2e/visual/myday.config.ts --project=visual
 *
 * ⚠️ `isMobile` and `hasTouch` on purpose. The glass look grows a button to
 * 40 px only under `pointer-coarse`, and a bare 390 px window is not coarse.
 */

const OUT = process.env.UX_OUT || "ux-shots/my-day-phone";

const SESSION = { user: { email: "asha@example.com", name: "Asha Rao" }, expires: "2099-01-01T00:00:00.000Z" };
const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "calendar", "knowledge", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();
const inHours = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();

type Act = Record<string, unknown>;
const tap = (id: string, label: string, act: string, ref: string): Act => ({ id, label, class: "A", way: "tap", act, act_ref: ref, ...(act === "read" ? { act_refs: [ref] } : {}) });
const open = (id: string, label: string, href: string): Act => ({ id, label, class: "A", way: "open", href });

let n = 0;
const card = (app: string, kind: string, group: string, title: string, why: string, at: string, href: string, acts: Act[]) => ({
  signal_id: `${app}:${++n}`,
  app,
  kind,
  folded: [],
  group,
  score: 100 - n,
  title,
  why,
  at,
  href,
  acts,
  state: n % 3 === 0 ? "new" : "seen",
  updated: false,
  still_open: false,
  visits: 1,
  state_fp: `a41f09${String(n).padStart(2, "0")}`,
});

const CARDS = [
  card("reminders", "reminder_due", "now", "Call the bank about the GST refund", "Due now.", hoursAgo(0.1), "/calendar?reminder=r1", [open("open-reminder", "Open", "/calendar?reminder=r1")]),
  card("calendar", "meeting_soon", "now", "Design review with Kim and Ravi", "Starts in 20 minutes. 3 people.", inHours(0.3), "/calendar", [open("open-day", "Open the day", "/calendar")]),
  card("tasks", "overdue", "needs_you", "Send the revised quote to Fracktal for the printer order", "Due yesterday. 1 day late.", hoursAgo(30), "/projects?task=t1", [tap("done", "Done", "done", "t1"), open("move-tomorrow", "Move to tomorrow", "/projects?task=t1")]),
  card("tasks", "due_today", "needs_you", "Review the Q4 hiring plan", "Due today at 17:00.", hoursAgo(2), "/projects?task=t2", [tap("done", "Done", "done", "t2"), open("block-time", "Block time", "/calendar")]),
  card("email", "needs_reply", "needs_you", "Re: Supplier contract renewal, pricing for 2027", "Ravi Kumar wrote 5 hours ago.", hoursAgo(5), "/email?email=a&account=acc1", [open("open-thread", "Open thread", "/email?email=a&account=acc1")]),
  card("projects", "mention", "needs_you", "Kim mentioned you on Launch checklist", "\"Can you confirm the venue by Friday?\"", hoursAgo(3), "/projects?task=t3", [tap("read", "Mark read", "read", "n1"), open("open", "Open", "/projects?task=t3")]),
  card("approvals", "approvals_waiting", "needs_you", "3 approvals wait for you", "The oldest waits 2 days.", hoursAgo(48), "/approvals", [open("open-approvals", "Open Approvals", "/approvals")]),
  card("knowledge", "kb_gap", "ready", "Your team asked about the travel policy 4 times", "No page answers it yet.", hoursAgo(20), "/knowledge/gaps", [open("write-page", "Write a page", "/knowledge/new"), open("open-gaps", "See all gaps", "/knowledge/gaps")]),
  card("projects", "waiting_on", "fyi", "Ravi has not moved the vendor onboarding task", "You asked 3 days ago.", hoursAgo(72), "/projects?task=t4", [open("nudge", "Nudge Ravi", "/projects?task=t4")]),
  card("email", "follow_up", "fyi", "No reply to your note to Meera about the demo", "Sent 4 days ago.", hoursAgo(96), "/email?email=b&account=acc1", [open("follow-up", "Follow up", "/email?email=b&account=acc1")]),
];

const NEEDS = {
  count: 5,
  total: 5,
  items: [],
  sources: { tasks: "ok", projects: "ok", email: "ok" },
  totals: { tasks: 2, projects: 1, email: 1 },
  capped: [],
};

const today = new Date();
const at = (h: number, m = 0) => {
  const d = new Date(today);
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};
const CALENDAR = {
  rows: [
    { id: "c1", title: "Stand-up", scheduled_start: at(9, 30), scheduled_end: at(9, 45), disposition: "NEXT" },
    { id: "c2", title: "Design review with Kim and Ravi", scheduled_start: at(11), scheduled_end: at(12), disposition: "NEXT" },
    { id: "c3", title: "Write the investor update", scheduled_start: at(14), scheduled_end: at(15, 30), disposition: "NEXT" },
    { id: "c4", title: "Call with Fracktal", scheduled_start: at(16), scheduled_end: at(16, 30), disposition: "NEXT" },
  ],
  total: 4,
};

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    for (const k of ["cc-shell-bar", "cc-shell-nav", "cc-shell-dock", "cc-shell-chat", "cc-my-day", "cc-my-day-feed"]) localStorage.setItem(k, "1");
  });
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/api/shell/feed/state") return json({ written: 0 });
    if (p === "/api/shell/feed") {
      const groups = ["now", "needs_you", "ready", "fyi"].map((id) => ({ id, count: CARDS.filter((c) => c.group === id).length }));
      return json({
        cards: CARDS,
        groups,
        needs: NEEDS,
        sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "ok", calendar: "ok", knowledge: "ok", reminders: "ok" },
        capped: [],
        reminders_ahead: [{ id: "r2", title: "Pay the office rent", fire_at: inHours(3) }],
        reminder_subjects: {},
        handled: [
          { app: "tasks", title: "Book the venue", href: "/projects?task=t9", at: hoursAgo(1), by: "you" },
        ],
        handled_ok: true,
      });
    }
    if (p === "/api/shell/needs") return json(NEEDS);
    if (p.startsWith("/api/projects/my/calendar")) return json(CALENDAR);
    if (p === "/api/auth/me/shell") return json(LAYOUT);
    if (p === "/api/auth/me") return json(MEMBER);
    if (p === "/api/auth/session") return json(SESSION);
    if (p === "/api/health") return json({ gateway: "up" });
    if (p === "/api/accounts") return json({ enabled: false });
    if (p === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (p.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (p === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(p)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
}

/** Each feed row: its width, the title's width, the acts' width and the tallest act. */
async function measure(page: Page) {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-feed-row]")].map((row) => {
      const r = row.getBoundingClientRect();
      const title = row.querySelector<HTMLElement>("h2");
      const acts = [...row.querySelectorAll<HTMLElement>("[data-feed-act], [data-feed-menu]")];
      const actsW = acts.reduce((s, a) => s + a.getBoundingClientRect().width, 0);
      return {
        id: row.dataset.testid,
        row: Math.round(r.width),
        height: Math.round(r.height),
        title: title ? Math.round(title.getBoundingClientRect().width) : 0,
        titleText: title?.textContent ?? "",
        acts: Math.round(actsW),
        tallest: Math.round(Math.max(0, ...acts.map((a) => a.getBoundingClientRect().height))),
        phoneActs: Math.round(row.querySelector<HTMLElement>("[data-feed-phone-acts]")?.getBoundingClientRect().width ?? 0),
        font: (() => {
          const a = title?.querySelector<HTMLElement>("a, span") ?? null;
          if (!a) return null;
          const cs = getComputedStyle(a);
          return { size: cs.fontSize, line: cs.lineHeight, h: Math.round(a.getBoundingClientRect().height), display: cs.display, overflow: cs.overflow };
        })(),
      };
    }),
  );
}

async function shoot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

test("My Day on a touch phone", async ({ page }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await setup(page);
  await page.goto("/");
  await page.getByTestId("my-day-feed").waitFor({ timeout: 120_000 });
  await page.waitForTimeout(2500);

  await shoot(page, "screen1--dark-390");
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await shoot(page, "screen1--light-390");

  // Scroll the page's own scroll area, a screen at a time.
  for (let i = 2; i <= 4; i++) {
    await page.evaluate(() => {
      const el = document.querySelector<HTMLElement>("[data-testid=my-day]")?.parentElement;
      el?.scrollBy(0, 700);
    });
    await shoot(page, `screen${i}--light-390`);
  }

  // The phone menu of a task row: its other act, then the card's menu.
  const more = page.getByRole("button", { name: /^More for Send the revised quote/ });
  if (await more.count()) {
    await more.first().scrollIntoViewIfNeeded();
    await more.first().tap();
    await shoot(page, "menu--light-390");
    await page.keyboard.press("Escape");
  }

  // The whole page in one frame: a tall window, so the inner scroll area holds it all.
  await page.setViewportSize({ width: 390, height: 3000 });
  await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>("[data-testid=my-day]")?.parentElement;
    el?.scrollTo(0, 0);
  });
  await shoot(page, "full--light-390");
  await page.evaluate(() => document.documentElement.classList.remove("light"));
  await shoot(page, "full--dark-390");

  const probe = await page.evaluate(() => {
    const a = [...document.querySelectorAll<HTMLElement>("[data-feed-row] h2 a")].find((x) => x.textContent?.startsWith("Re: Supplier"));
    if (!a) return null;
    const node = a.firstChild as Text | null;
    const text = node?.textContent ?? "";
    const at = text.indexOf("pricing");
    const r = document.createRange();
    if (node && at >= 0) {
      r.setStart(node, at);
      r.setEnd(node, at + 1);
    }
    const cs = getComputedStyle(a);
    const h2 = a.parentElement!;
    return {
      a: a.getBoundingClientRect().toJSON(),
      p: node && at >= 0 ? r.getBoundingClientRect().toJSON() : null,
      h2: h2.getBoundingClientRect().toJSON(),
      css: { pl: cs.paddingLeft, ml: cs.marginLeft, ti: cs.textIndent, ls: cs.letterSpacing, ow: cs.overflowWrap, ws: cs.whiteSpace, tf: cs.transform, cls: a.className },
      h2css: { overflow: getComputedStyle(h2).overflow, display: getComputedStyle(h2).display },
    };
  });
  fs.writeFileSync(path.join(OUT, "rows.json"), JSON.stringify({ rows: await measure(page), probe, errors }, null, 2));
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 });
  test("My Day on a desktop is unchanged", async ({ page }) => {
    test.setTimeout(300_000);
    await setup(page);
    await page.goto("/");
    await page.getByTestId("my-day-feed").waitFor({ timeout: 120_000 });
    await page.waitForTimeout(2500);
    await page.evaluate(() => document.documentElement.classList.add("light"));
    await shoot(page, "desktop--light-1440");
    await page.setViewportSize({ width: 700, height: 1000 });
    await shoot(page, "narrow--light-700");
  });
});
