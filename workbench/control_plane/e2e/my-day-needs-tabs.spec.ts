import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * WS-MDC CC-11 (D-MD-9): "Needs you" on My Day has one tab for each app that
 * has rows waiting on the member. The rules are in `src/lib/shell/needsTabs.ts`.
 *
 * Mutations, run by hand (2026-11-07):
 *   - Always draw a Projects tab, with a count of 0: "the tabs and their
 *     counts" fails on the list of tabs, and so does the arrow keys test.
 *   - Stop filtering by app (`shownGroups` returns `groups`): "a tab shows
 *     only its app rows" fails on the My work tab.
 *   - Drop `onKeyDown` from `Tabs.tsx`: "the arrow keys" fails.
 *   - Let the bar wrap (`flex-wrap` on the tablist, no `overflow-x-auto`):
 *     "one line at 390 px" fails on the height.
 */

const SESSION = { user: { email: "asha@example.com", name: "Asha Rao" }, expires: "2099-01-01T00:00:00.000Z" };
const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };
const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();

const card = (app: string, id: string, kind: string, title: string, href: string) => ({
  signal_id: `${app}:${id}`,
  app,
  kind,
  folded: [],
  group: "needs_you",
  score: 50,
  title,
  why: "Waiting.",
  at: hoursAgo(5),
  href,
  acts: [],
});

/** Two tasks, one reply, one approval. Projects has no row. */
const CARDS = [
  card("tasks", "t1", "overdue", "Send the quote", "/projects?task=t1"),
  card("tasks", "t2", "overdue", "Book the venue", "/projects?task=t2"),
  card("email", "a", "needs_reply", "Re: the supplier contract", "/email?email=a&account=acc1"),
  card("approvals", "p1", "approvals_waiting", "3 approvals wait for you", "/approvals"),
];

const NEEDS = { count: 4, total: 4, items: [], sources: {}, totals: {}, capped: [] };

async function setup(page: Page) {
  await page.addInitScript(() => {
    for (const k of ["cc-shell-bar", "cc-shell-nav", "cc-shell-dock", "cc-my-day", "cc-my-day-feed"]) localStorage.setItem(k, "1");
  });
  await page.route("**/api/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/shell/feed")
      return json({
        cards: CARDS,
        groups: [{ id: "needs_you", count: CARDS.length }],
        needs: NEEDS,
        sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "ok", calendar: "ok" },
        capped: [],
      });
    if (path === "/api/shell/feed/state") return json({ written: 0 });
    if (path === "/api/shell/needs") return json(NEEDS);
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
}

const feed = (page: Page) => page.getByTestId("my-day-feed");
const feedCard = (page: Page, id: string) => page.getByTestId(`feed-card-${id}`);
const tabs = (page: Page) => feed(page).getByRole("tab");

test.describe("My Day, the Needs you tabs (CC-11)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the tabs and their counts match the rows, and an app with no row has no tab", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    // All first, then the apps by count. The count is the number of rows.
    await expect(tabs(page)).toHaveText(["All4", "My work2", "Email1", "Approvals1"]);
    await expect(tabs(page).first()).toHaveAttribute("aria-selected", "true");
    await expect(feed(page).getByRole("tab", { name: /^Projects/ })).toHaveCount(0);
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(4);
    // The tabs are one tablist that controls one panel.
    await expect(feed(page).getByRole("tablist")).toHaveCount(1);
    await expect(tabs(page).first()).toHaveAttribute("aria-controls", "feed-needs-panel");
    await expect(page.locator("#feed-needs-panel")).toHaveAttribute("role", "tabpanel");
  });

  test("a tab shows only its app rows, and See all opens the list of Approvals", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await feed(page).getByRole("tab", { name: /^My work/ }).click();
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(feedCard(page, "tasks:t2")).toBeVisible();
    await expect(feedCard(page, "email:a")).toHaveCount(0);
    await expect(feedCard(page, "approvals:p1")).toHaveCount(0);
    await expect(feed(page).locator("[data-feed-see-all]")).toHaveCount(0);

    await feed(page).getByRole("tab", { name: /^Approvals/ }).click();
    await expect(feedCard(page, "approvals:p1")).toBeVisible();
    await expect(feedCard(page, "tasks:t1")).toHaveCount(0);
    await expect(feed(page).locator("[data-feed-see-all]")).toHaveAttribute("href", "/approvals");

    await tabs(page).first().click();
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(4);
  });

  test("the arrow keys, Home and End move the selection", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const selected = () => feed(page).locator('[role="tab"][aria-selected="true"]');
    await tabs(page).first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(selected()).toContainText("My work");
    await expect(selected()).toBeFocused();
    await expect(feedCard(page, "email:a")).toHaveCount(0);
    await page.keyboard.press("End");
    await expect(selected()).toContainText("Approvals");
    await page.keyboard.press("ArrowRight");
    await expect(selected()).toContainText("All");
    await page.keyboard.press("ArrowLeft");
    await expect(selected()).toContainText("Approvals");
    await page.keyboard.press("Home");
    await expect(selected()).toContainText("All");
  });
});

test.describe("My Day, the Needs you tabs on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("the bar is one line at 390 px, and the page does not scroll sideways", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const bar = (await feed(page).locator("[data-feed-filter]").boundingBox())!;
    expect(bar.height).toBeLessThan(52);
    const sideways = await page.evaluate(() => {
      const all = [document.documentElement, document.body, ...document.querySelectorAll<HTMLElement>("main, [data-testid=my-day]")];
      return all.some((el) => el.scrollWidth > el.clientWidth + 1);
    });
    expect(sideways).toBe(false);
    // A tap on a tab filters the rows.
    await feed(page).getByRole("tab", { name: /^Email/ }).tap();
    await expect(feedCard(page, "email:a")).toBeVisible();
    await expect(feedCard(page, "tasks:t1")).toHaveCount(0);
  });
});
