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

const OUT = process.env.UX_OUT || "ux-shots/cc11";

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

const LOTS = Array.from({ length: 25 }, (_, i) =>
  card("tasks", "overdue", "needs_you", `Extra task number ${i + 1}`, "Due last week.", hoursAgo(200 + i), `/projects?task=x${i}`, [tap("done", "Done", "done", `x${i}`)]),
);

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
        cards: process.env.CC11_LOTS ? [...CARDS, ...LOTS] : CARDS,
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


async function shoot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}
const mode = (page: Page, light: boolean) => page.evaluate((l) => document.documentElement.classList.toggle("light", l), light);

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });
  test("1440", async ({ page }) => {
    test.setTimeout(300_000);
    await setup(page);
    await page.goto("/");
    await page.getByTestId("my-day-feed").waitFor({ timeout: 120_000 });
    await page.waitForTimeout(2500);
    await mode(page, true);
    await shoot(page, "light-1440");
    for (const light of [true, false]) {
      await mode(page, light);
      const chain = await page.evaluate(() => {
        const out: string[] = [];
        let el: HTMLElement | null = document.querySelector("[data-feed-filter]");
        while (el) {
          const cs = getComputedStyle(el);
          out.push(`${el.tagName}.${String(el.className).slice(0, 90)} bg=${cs.backgroundColor} img=${cs.backgroundImage.slice(0, 40)}`);
          el = el.parentElement;
        }
        const root = getComputedStyle(document.documentElement);
        out.push(`--background=${root.getPropertyValue("--background")} body=${getComputedStyle(document.body).backgroundColor}`);
        return out;
      });
      fs.mkdirSync(OUT, { recursive: true });
      fs.writeFileSync(path.join(OUT, `chain-${light ? "light" : "dark"}.txt`), chain.join(String.fromCharCode(10)));
    }
    await mode(page, true);
    await shoot(page, "many-tabs-fade-chevron-1440");
    const nx = page.locator("[data-feed-tabs-next]");
    if (await nx.count()) {
      await nx.click();
      await shoot(page, "many-tabs-after-chevron-1440");
      await mode(page, false);
      await shoot(page, "dark-left-cap-1440");
      await mode(page, true);
    }
    await mode(page, false);
    await shoot(page, "dark-1440");
    await mode(page, true);
    await page.evaluate(() => document.documentElement.style.setProperty("--ui-scale", "0.875"));
    await shoot(page, "compact-1440");
    await page.evaluate(() => {
      const d = document.documentElement.style;
      d.setProperty("--ui-scale", "1");
      for (const p of ["--primary", "--ring", "--sidebar-primary"]) d.setProperty(p, "#d9480f");
    });
    await shoot(page, "accent-1440");
    await page.evaluate(() => {
      const d = document.documentElement.style;
      for (const p of ["--primary", "--ring", "--sidebar-primary"]) d.removeProperty(p);
    });
    if (process.env.CC11_LOTS) {
      await page.getByRole("tab", { name: /^My work/ }).click();
      await shoot(page, "tab-mywork-many-1440");
      return;
    }
    const nx2 = page.locator("[data-feed-tabs-next]");
    for (let i = 0; i < 4 && (await nx2.count()); i++) {
      await nx2.click();
      await page.waitForTimeout(500);
    }
    await page.getByRole("tab").filter({ hasText: /^Reminders/ }).click();
    await shoot(page, "tab-reminders-end-1440");
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  test("390", async ({ page }) => {
    test.setTimeout(300_000);
    await setup(page);
    await page.goto("/");
    await page.getByTestId("my-day-feed").waitFor({ timeout: 120_000 });
    await page.waitForTimeout(2500);
    await mode(page, true);
    await shoot(page, "light-390");
    await mode(page, false);
    await shoot(page, "dark-390");
    await mode(page, true);
    const tab = page.getByRole("tab", { name: /^Projects/ });
    if (await tab.count()) {
      await tab.tap();
      await shoot(page, "tab-projects-390");
    }
  });
});
