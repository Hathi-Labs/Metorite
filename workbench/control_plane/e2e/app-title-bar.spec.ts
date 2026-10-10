import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * Every app opens with ONE title bar, and the bar never loses a control
 * (owner, 2026-10-10. `navigation_shell.md` §3.1, `AGENTS.md` rule 11).
 *
 * This is the runtime half of `src/lib/shell/appBar.test.ts`. That file reads
 * source, so it cannot see a bar behind a branch that never renders. This one
 * opens each page and counts.
 *
 *   1. Every live pane, My Day, and the four Settings sub-pages draw exactly
 *      one `<h1>` and exactly one app bar, at 1440 and at 390.
 *   2. At 1024 with the sidebar OPEN, Calendar (with every tool up), Email
 *      and Projects keep every control of the bar inside the window, and the
 *      name overlaps no control. Measured in round 2: Month ended at x=1095,
 *      and the h1 painted over "< Today".
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  name: "Asha Rao",
  is_admin: true,
  features: ["tasks", "email", "whatsapp", "projects", "people", "chat", "approvals"],
  permissions: ["admin:members:read", "admin:members:manage"],
  capabilities: ["admin:members:read", "admin:members:manage"],
  roles: ["owner"],
  organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
};

const ACCOUNT = {
  id: "acc1",
  provider: "microsoft",
  email_address: "asha@acme.example",
  label: "Work",
  avatar_color: "#6366f1",
  unread_count: 3,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
  onboarding_done: true,
};

const MEMBER_ACCESS = {
  email: "priya@acme.example",
  display_name: "Priya Rao",
  status: "active",
  roles: [],
  overrides: [],
  features: [],
  capabilities: [],
  agents: [],
  integrations: [],
  granted: [],
  denied: [],
};

/** Three tasks that put every Calendar tool up: due soon, done today, and replan. */
function calendarRows() {
  const at = (h: number, m = 0) => {
    const d = new Date();
    d.setHours(h, m, 0, 0);
    return d.toISOString();
  };
  const base = { is_mine: true, project_id: "p1", created_at: at(0), updated_at: at(0) };
  return [
    // An unscheduled next action due in three days: the due-soon pill.
    { ...base, id: "t1", title: "Send the quote", disposition: "NEXT", due_at: new Date(Date.now() + 3 * 86400000).toISOString() },
    // A block done today: Review.
    { ...base, id: "t2", title: "Stand-up", disposition: "DONE", scheduled_start: at(0, 5), scheduled_end: at(0, 35), completed_at: at(0, 35) },
    // A flexible block still open today: Fit what's left.
    { ...base, id: "t3", title: "Draft the plan", disposition: "NEXT", flexible: true, scheduled_start: at(23, 0), scheduled_end: at(23, 30) },
  ];
}

async function setup(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
    // One conversation, so Chat opens it rather than its agent picker.
    const now = new Date().toISOString();
    localStorage.setItem(
      "cc-chat::admin@example.com|org1::sessions",
      JSON.stringify([{ id: "s1", name: "Supplier follow-up", agentName: "assistant", createdAt: now, updatedAt: now, messageCount: 0 }]),
    );
  });
  const rows = calendarRows();
  await stubApi(
    page,
    {
      "auth/me": ADMIN,
      health: { gateway: "up" },
      "email/accounts": [ACCOUNT],
      "accounts/acc1/folders": [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 12, unread_count: 3 }],
      "accounts/acc1/labels": [],
      "email/messages/facets": { folder: "inbox", total: 0, unread: 0, uncategorized: 0, labels: {} },
      "email/messages": { emails: [], total: 0, page: 1, page_size: 50 },
      "projects/my/inbox": { rows, total: rows.length },
      "people/facets": { departments: [], teams: [], statuses: [] },
      "actions/pending": [],
      "admin/members/requests": [],
      "example/access": MEMBER_ACCESS,
      "admin/members": [],
      "admin/roles": [],
      "admin/groups": [],
      "admin/features": [],
      agents: [],
    },
    { arrayPaths: /^(notes|chat|agent|apps|agents)\b/ },
  );
  // Billing reads the Customer Console, which no test runs. It answers 503
  // there, and the page draws its unavailable state under its bar.
  await page.route("**/api/billing/**", (r) =>
    r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Billing is not configured" }) }),
  );
}

/** Every page that opens with the bar, by its route. */
const PAGES: Array<[string, string]> = [
  ["My Day", "/"],
  ["My Tasks", "/tasks"],
  ["Calendar", "/calendar"],
  ["My Profile", "/people/me"],
  ["My Email", "/email"],
  ["My WhatsApp", "/whatsapp"],
  ["Projects", "/projects"],
  ["People", "/people"],
  ["Chat", "/chat"],
  ["Approvals", "/approvals"],
  ["Organisation", "/settings/organization"],
  ["Appearance", "/settings/appearance"],
  ["Teams", "/settings/groups"],
  ["Roles", "/settings/roles"],
  ["Billing", "/settings/billing"],
  ["a member", "/settings/members/priya%40acme.example"],
];

/**
 * The one named exception, on the phone only. Chat opens there with the
 * chat's own header (the agent and Share) under the bottom tabs, and it had
 * no h1 before round 2 either. A compact bar above it would stack a third
 * row on the smallest screen, for a name the Chats tab already says.
 */
const PHONE_WITHOUT_BAR = new Set(["/chat"]);

async function counts(page: Page, path: string) {
  const errors: string[] = [];
  const onError = (e: Error) => errors.push(String(e.stack || e).slice(0, 600));
  page.on("pageerror", onError);
  await page.goto(path);
  await expect(
    page.locator("[data-app-bar]").or(page.locator("main textarea")).first(),
    [path, ...errors].join(" | "),
  ).toBeVisible({ timeout: 20_000 });
  page.off("pageerror", onError);
  await page.waitForTimeout(600);
  return page.evaluate(() => ({
    h1: Array.from(document.querySelectorAll("h1")).filter((h) => h.getClientRects().length > 0).length,
    bars: Array.from(document.querySelectorAll("[data-app-bar]")).filter((b) => b.getClientRects().length > 0).length,
  }));
}

test.describe("one title bar and one h1 on every page", () => {
  test("at 1440", async ({ page }) => {
    test.setTimeout(300_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    for (const [name, path] of PAGES) {
      expect(await counts(page, path), `${name} (${path})`).toEqual({ h1: 1, bars: 1 });
    }
  });

  test("at 390, the phone", async ({ browser }) => {
    test.setTimeout(300_000);
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    await setup(page);
    for (const [name, path] of PAGES) {
      const want = PHONE_WITHOUT_BAR.has(path) ? { h1: 0, bars: 0 } : { h1: 1, bars: 1 };
      expect(await counts(page, path), `${name} (${path}) on a phone`).toEqual(want);
    }
    // Each phone bar is the compact one.
    await page.goto("/settings/organization");
    await expect(page.locator('[data-app-bar="compact"]')).toBeVisible();
    await ctx.close();
  });
});

/** Every control of the bar: inside the window, and clear of the name. */
async function barFits(page: Page, width: number) {
  return page.locator('[data-app-bar="desktop"]').evaluate((bar, w) => {
    const h1 = bar.querySelector("h1")!.getBoundingClientRect();
    const controls = Array.from(bar.querySelectorAll("button, a, [role='button'], input"))
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && r.height > 0);
    const out: string[] = [];
    for (const { el, r } of controls) {
      const label = el.getAttribute("aria-label") || el.textContent?.trim() || el.tagName;
      if (r.left < 0 || r.right > w + 0.5) out.push(`${label} leaves the window (${Math.round(r.left)}..${Math.round(r.right)})`);
      const overlap = r.left < h1.right - 0.5 && r.right > h1.left + 0.5 && r.top < h1.bottom - 0.5 && r.bottom > h1.top + 0.5;
      if (overlap && !el.contains(bar.querySelector("h1")) && !bar.querySelector("h1")!.contains(el)) {
        out.push(`the name overlaps ${label}`);
      }
      // The control is the thing a press at its centre lands on.
      const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
      if (hit && !el.contains(hit) && !hit.contains(el)) out.push(`${label} is covered by ${hit.tagName}`);
    }
    if (bar.scrollWidth > bar.clientWidth + 1) out.push(`the bar scrolls (${bar.scrollWidth} > ${bar.clientWidth})`);
    return { problems: out, controls: controls.length };
  }, width);
}

test.describe("at 1024 with the sidebar open, nothing leaves the bar", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("Calendar, with every tool up", async ({ page }) => {
    await setup(page);
    await page.goto("/calendar");
    const bar = page.locator('[data-app-bar="desktop"]');
    await expect(bar.getByRole("heading", { level: 1, name: "Calendar" })).toBeVisible();
    // The maximal state was reached: each of these is in the bar.
    await expect(bar.getByText(/^1/).first()).toBeVisible();
    for (const name of ["Start day", "Review", "Fit what's left", "Calendar settings", "Previous", "Next"]) {
      await expect(bar.getByRole("button", { name }), name).toBeVisible();
    }
    await expect(bar.getByRole("button", { name: "Month" })).toBeVisible();
    const fit = await barFits(page, 1024);
    expect(fit.problems).toEqual([]);
    expect(fit.controls).toBeGreaterThan(8);
  });

  for (const [name, path] of [["My Email", "/email"], ["Projects", "/projects"]] as const) {
    test(name, async ({ page }) => {
      await setup(page);
      await page.goto(path);
      await expect(page.locator('[data-app-bar="desktop"]').getByRole("heading", { level: 1, name })).toBeVisible();
      await page.waitForTimeout(500);
      expect((await barFits(page, 1024)).problems).toEqual([]);
    });
  }
});
