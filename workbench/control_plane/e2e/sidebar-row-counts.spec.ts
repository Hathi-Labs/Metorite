import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The sidebar's row counts in a browser (owner, 2026-10-11). Each app row
 * shows what needs the member there, from the one feed of My Day and the
 * bell (`src/lib/shell/navNeeds.ts`, `useNavNeeds.ts`).
 *
 * Each test is written so the obvious wrong build fails it:
 *   1. Email 3 and Tasks 2: the rows say 3 and 2, in a quiet number, and the
 *      link names say ", 3 need you" and ", 2 need you".
 *   2. A failed email source: no number on Email, never a 0. A source that
 *      fails on a later poll drops its number, and never keeps the old one.
 *   3. Hiding Tasks moves its 2 onto "Add apps".
 *   4. Hover on a row still shows its "···", and the count gives way.
 *   5. The folded rail shows a dot, and the name keeps the number.
 *   6. A Done in My Day takes the task out of the count at once.
 *   7. The page reads the feed once: no second fetch for the sidebar.
 *   8. The phone drawer shows the same counts.
 *
 * Mutation, seen to fail first: drop `needs={appNeeds[p.href]}` from the
 * open rail's `NavLink` and cases 1, 3 and 4 fail.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "priya@example.com",
  name: "Priya Shah",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

/** Every app MEMBER may star, in nav.ts order. */
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];

/** A stored layout that stars every app, so every row shows. */
const LAYOUT = {
  preset: "engineer",
  answered: "answered",
  pins: KNOWN,
  known: KNOWN,
  cardOrder: null,
  newOrder: null,
};

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();

const task = (id: string, title: string) => ({
  id: `tasks:${id}`,
  app: "tasks",
  kind: "overdue",
  title,
  detail: null,
  href: `/projects?task=${id}`,
  at: hoursAgo(30),
  act: "done",
  act_ref: id,
});

const reply = (id: string) => ({
  id: `email:acc1:${id}`,
  app: "email",
  kind: "needs_reply",
  title: `Re: thread ${id}`,
  detail: "From Ravi",
  href: `/email?email=${id}&account=acc1`,
  at: hoursAgo(5),
  act: null,
  act_ref: null,
});

/** Email 3 and Tasks 2, all sent, and Projects with nothing. */
function feed(email: "ok" | "failed" = "ok") {
  const items = [task("t1", "Send the quote"), task("t2", "Book the venue"), ...(email === "ok" ? [reply("a"), reply("b"), reply("c")] : [])];
  return {
    count: items.length,
    total: items.length,
    items,
    sources: { tasks: "ok", projects: "ok", email },
    totals: email === "ok" ? { tasks: 2, projects: 0, email: 3 } : { tasks: 2, projects: 0 },
  };
}

/** The one task the My Tasks store loads, so a Done from the feed finds it. */
const LENS_ROW = {
  id: "t1",
  title: "Send the quote",
  is_mine: true,
  project_id: "p1",
  disposition: "NEXT",
  due_at: hoursAgo(30),
  created_at: hoursAgo(48),
  updated_at: hoursAgo(48),
};

interface Store {
  needsGets: number;
  /** The email source the next answer reports. A test may flip it. */
  email: "ok" | "failed";
}

async function stub(
  page: Page,
  opts: { email?: "ok" | "failed"; folded?: boolean; myDay?: boolean; nav?: boolean } = {},
): Promise<Store> {
  const store: Store = { needsGets: 0, email: opts.email ?? "ok" };
  let layout: Record<string, unknown> = { ...LAYOUT };
  await page.addInitScript(
    ({ folded, myDay, nav }) => {
      localStorage.setItem("cc-shell-bar", "1");
      localStorage.setItem("cc-shell-nav", nav ? "1" : "0");
      localStorage.removeItem("cc-shell-dock");
      if (myDay) localStorage.setItem("cc-my-day", "1");
      else localStorage.removeItem("cc-my-day");
      if (folded) localStorage.setItem("cc-sidebar-collapsed", "1");
      else localStorage.removeItem("cc-sidebar-collapsed");
    },
    { folded: !!opts.folded, myDay: !!opts.myDay, nav: opts.nav ?? true },
  );
  // One router, on purpose (`org-branding.spec.ts` says why).
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") {
      if (req.method() === "PUT") layout = { ...layout, ...(req.postDataJSON() as Record<string, unknown>) };
      return json(layout);
    }
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") {
      store.needsGets += 1;
      return json(feed(store.email));
    }
    if (path === "/api/projects/my/inbox") return json({ rows: [LENS_ROW], total: 1 });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
  return store;
}

const sidebar = (page: Page) => page.locator("aside[data-collapsed]");
const row = (page: Page, href: string) => sidebar(page).locator(`[data-nav-row="${href}"]`);
const count = (page: Page, href: string) => row(page, href).locator('[data-nav-needs="count"]');

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Email 3 and Tasks 2: each row says its number, quietly, and its name says it in words", async ({ page }) => {
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(count(page, "/email")).toHaveText("3");
    await expect(count(page, "/tasks")).toHaveText("2");
    await expect(sidebar(page).getByRole("link", { name: "My Email, 3 need you", exact: true })).toBeVisible();
    await expect(sidebar(page).getByRole("link", { name: "My Tasks, 2 need you", exact: true })).toBeVisible();
    // Projects answered with nothing, so its row shows nothing.
    await expect(row(page, "/projects").locator("[data-nav-needs]")).toHaveCount(0);
    await expect(sidebar(page).getByRole("link", { name: "Projects", exact: true })).toBeVisible();
    // Quiet: the muted text token, no fill, and never the badge vocabulary.
    await expect(count(page, "/email")).toHaveClass(/text-muted-foreground/);
    await expect(count(page, "/email")).not.toHaveClass(/bg-/);
    await expect(row(page, "/email").locator("[data-nav-badge]")).toHaveCount(0);
  });

  test("a failed email source shows no number on Email, never a 0", async ({ page }) => {
    await stub(page, { email: "failed" });
    await page.goto("/settings/appearance");
    await expect(count(page, "/tasks")).toHaveText("2");
    await expect(row(page, "/email")).toBeVisible();
    await expect(row(page, "/email").locator("[data-nav-needs]")).toHaveCount(0);
    await expect(sidebar(page).getByRole("link", { name: "My Email", exact: true })).toBeVisible();
  });

  test("the poll reads the feed again, and a source that fails then drops its number, never keeps it", async ({ page }) => {
    await page.clock.install();
    const store = await stub(page);
    await page.goto("/settings/appearance");
    await expect(count(page, "/email")).toHaveText("3");
    // The next answer says the email source failed. The one poll reads it.
    store.email = "failed";
    const before = store.needsGets;
    await page.clock.fastForward(61_000);
    await expect.poll(() => store.needsGets).toBeGreaterThan(before);
    await expect(row(page, "/email").locator("[data-nav-needs]")).toHaveCount(0);
    await expect(sidebar(page).getByRole("link", { name: "My Email", exact: true })).toBeVisible();
    await expect(count(page, "/tasks")).toHaveText("2");
  });

  test("hiding Tasks moves its 2 onto Add apps", async ({ page }) => {
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(count(page, "/tasks")).toHaveText("2");
    const addApps = sidebar(page).locator("[data-nav-add-apps] button");
    await expect(addApps.locator('[data-nav-needs="count"]')).toHaveCount(0);

    await row(page, "/tasks").getByRole("link").click({ button: "right" });
    await page.getByRole("menuitem", { name: "Hide from sidebar" }).click();
    await expect(row(page, "/tasks")).toHaveCount(0);
    await expect(addApps.locator('[data-nav-needs="count"]')).toHaveText("2");
    await expect(addApps).toHaveAccessibleName("Add apps, 2 need you in hidden apps");
    // Email keeps its own row and its own number.
    await expect(count(page, "/email")).toHaveText("3");
  });

  test("hover on a row still shows its ···, and the count gives way", async ({ page }) => {
    await stub(page);
    await page.goto("/settings/appearance");
    const more = row(page, "/email").getByRole("button", { name: "More for My Email" });
    // The slot that holds the count (`rail-row-meta`), which the CSS clips.
    const slot = row(page, "/email").locator(".rail-row-meta");
    await expect(count(page, "/email")).toBeVisible();
    expect((await slot.boundingBox())?.width ?? 0).toBeGreaterThan(2);
    await row(page, "/email").hover();
    await expect(more).toBeVisible();
    // The count is clipped from sight, and the name still says it.
    await expect.poll(async () => (await slot.boundingBox())?.width ?? 0).toBeLessThanOrEqual(1);
    await expect(sidebar(page).getByRole("link", { name: "My Email, 3 need you", exact: true })).toBeVisible();
    await more.click();
    await expect(page.getByRole("menuitem", { name: "Hide from sidebar" })).toBeVisible();
  });

  test("the folded rail shows a quiet dot, and the name keeps the number", async ({ page }) => {
    await stub(page, { folded: true });
    await page.goto("/settings/appearance");
    await expect(sidebar(page)).toHaveAttribute("data-collapsed", "true");
    const email = row(page, "/email");
    await expect(email.locator('[data-nav-needs="dot"]')).toHaveCount(1);
    await expect(email).toHaveAccessibleName("My Email, 3 need you");
    await expect(email).toHaveAttribute("title", /\(3 need you\)$/);
    await expect(row(page, "/projects").locator("[data-nav-needs]")).toHaveCount(0);
  });

  test("a Done in My Day takes the task out of the Tasks count at once", async ({ page }) => {
    await stub(page, { myDay: true });
    await page.goto("/");
    await expect(page.getByTestId("needs-you").getByText("Send the quote")).toBeVisible();
    await expect(count(page, "/tasks")).toHaveText("2");
    await page.getByTestId("needs-you").getByRole("button", { name: "Mark Send the quote done" }).click();
    await expect(count(page, "/tasks")).toHaveText("1");
    await expect(sidebar(page).getByRole("link", { name: "My Tasks, 1 needs you", exact: true })).toBeVisible();
  });

  test("the sidebar adds no fetch of its own: My Day reads the feed as often with it as without it", async ({ browser }) => {
    // My Day alone reads the feed twice on a first load: the identity bind
    // empties the cache once (`bindIdentity`). That is the baseline. The
    // sidebar reads the same key, so it must add no request to it.
    const reads = async (nav: boolean) => {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      const store = await stub(page, { myDay: true, nav });
      await page.goto("/");
      await expect(page.getByTestId("needs-you").getByText("Send the quote")).toBeVisible();
      if (nav) await expect(count(page, "/email")).toHaveText("3");
      await page.waitForTimeout(2500);
      await page.close();
      return store.needsGets;
    };
    const without = await reads(false);
    const withRows = await reads(true);
    expect(without).toBeGreaterThan(0);
    expect(withRows).toBe(without);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the Menu drawer shows the same counts in its rows", async ({ page }) => {
    await stub(page);
    await page.goto("/settings/appearance");
    await page.getByRole("button", { name: "Menu" }).click();
    const email = page.getByRole("link", { name: "My Email, 3 need you", exact: true });
    await expect(email).toBeVisible();
    await expect(email.locator('[data-nav-needs="count"]')).toHaveText("3");
    await expect(page.getByRole("link", { name: "My Tasks, 2 need you", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Projects", exact: true })).toBeVisible();
  });
});
