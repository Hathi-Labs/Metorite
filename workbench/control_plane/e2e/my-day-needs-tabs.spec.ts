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
 *   - Count the cut rows (read `groups` and not `fullGroups` in FeedCard):
 *     "a tab counts every row" fails on 20 and not 25.
 *   - Show the band heading under the Reminders tab (`tabbed` false): the
 *     Reminders tab test fails on the hidden heading.
 *   - Paint a cap `bg-background` (not `bg-card`): "a cap paints the surface" fails in dark.
 *   - Drop the scroll effect in NeedsTabBar: "scrolls clear of the left cap" fails.
 *   - Give All a count (needsTabs.ts): "All draws no count" fails.
 *   - Draw the chevron always (drop the `edge.right` test in NeedsTabBar):
 *     "a bar that fits draws no chevron" fails.
 *   - Drop the chevron's onClick: "the last tab is reachable" fails.
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
  // A card with a state_fp draws the menu, so a test can dismiss it.
  state: "seen",
  updated: false,
  still_open: false,
  visits: 1,
  state_fp: [...`${app}${id}`].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7).toString(16).padStart(8, "0"),
});

/** Two tasks, one reply, one approval. Projects has no row. */
const CARDS = [
  card("tasks", "t1", "overdue", "Send the quote", "/projects?task=t1"),
  card("tasks", "t2", "overdue", "Book the venue", "/projects?task=t2"),
  card("email", "a", "needs_reply", "Re: the supplier contract", "/email?email=a&account=acc1"),
  card("approvals", "p1", "approvals_waiting", "3 approvals wait for you", "/approvals"),
];

/** Eight apps with a row each, so the bar must scroll at 1440. */
const MANY = [
  ...CARDS,
  card("projects", "p", "mention", "Kim mentioned you", "/projects?task=t3"),
  card("calendar", "c", "meeting_soon", "Design review", "/calendar"),
  card("knowledge", "k", "kb_gap", "Travel policy gap", "/knowledge/gaps"),
  card("crm", "d", "deal_stale", "Deal went quiet", "/crm"),
  card("people", "q", "absence", "Leave request", "/people"),
];

const NEEDS = { count: 4, total: 4, items: [], sources: {}, totals: {}, capped: [] };

interface Opts {
  cards?: ReturnType<typeof card>[];
  /** One assistant run that waits on the member. */
  ai?: boolean;
  /** Two reminders of later today, so the band has two rows. */
  reminders?: boolean;
}

async function setup(page: Page, opts: Opts | ReturnType<typeof card>[] = {}) {
  const o: Opts = Array.isArray(opts) ? { cards: opts } : opts;
  const hidden = new Set<string>();
  await page.addInitScript(() => {
    for (const k of ["cc-shell-bar", "cc-shell-nav", "cc-shell-dock", "cc-my-day", "cc-my-day-feed"]) localStorage.setItem(k, "1");
  });
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/shell/feed") {
      const cards = (o.cards ?? CARDS).filter((c) => !hidden.has(c.signal_id));
      return json({
        cards,
        groups: [{ id: "needs_you", count: cards.length }],
        needs: NEEDS,
        sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "ok", calendar: "ok" },
        capped: [],
        ...(o.reminders
          ? {
              reminders_ahead: [
                { id: "r1", title: "Call the bank", fire_at: new Date(Date.now() + 3 * 3_600_000).toISOString() },
                { id: "r2", title: "Pay the rent", fire_at: new Date(Date.now() + 4 * 3_600_000).toISOString() },
              ],
              reminder_subjects: {},
            }
          : {}),
      });
    }
    if (path === "/api/shell/feed/state") {
      const body = req.postDataJSON() as { items: Record<string, unknown>[] };
      for (const item of body.items) {
        if (["snooze", "dismiss", "not_useful"].includes(String(item.action))) hidden.add(String(item.signal_id));
      }
      return json({ written: body.items.length });
    }
    if (path === "/api/shell/needs") return json(NEEDS);
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions")
      return json(
        o.ai
          ? [{ threadId: "th1", agentName: "Researcher", title: "Which supplier do you prefer?", startedAt: hoursAgo(1), state: "needs_input", askKind: "questions" }]
          : [],
      );
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
    await expect(tabs(page)).toHaveText(["All", "My work2", "Email1", "Approvals1"]);
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

test.describe("My Day, the Needs you bar with many tabs (CC-11)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("All draws no count", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(tabs(page).first()).toHaveText("All");
  });

  test("a bar that fits draws no chevron", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(page.locator("[data-feed-tabs-next], [data-feed-tabs-prev]")).toHaveCount(0);
  });

  test("with 8 tabs the last tab is reachable through the chevron", async ({ page }) => {
    await setup(page, MANY);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const next = feed(page).locator("[data-feed-tabs-next]");
    const prev = feed(page).locator("[data-feed-tabs-prev]");
    await expect(tabs(page)).toHaveCount(9);
    await expect(next).toBeVisible();
    await expect(prev).toHaveCount(0);
    await expect(next).toHaveAccessibleName("Show more tabs");
    const last = tabs(page).last();
    const bar = feed(page).getByRole("tablist");
    const inside = async () => {
      const t = (await last.boundingBox())!;
      const b = (await bar.boundingBox())!;
      return t.x >= b.x - 1 && t.x + t.width <= b.x + b.width + 1;
    };
    for (let i = 0; i < 4 && !(await inside()); i++) {
      await next.click();
      await page.waitForTimeout(500);
    }
    expect(await inside()).toBe(true);
    // The right edge is reached: its chevron goes, the left one comes.
    await expect(next).toHaveCount(0);
    await expect(prev).toBeVisible();
  });

  test("a cap paints the surface behind the bar, in light and in dark", async ({ page }) => {
    await setup(page, MANY);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(feed(page).locator("[data-feed-tabs-next]")).toBeVisible();
    for (const light of [true, false]) {
      await page.evaluate((l) => document.documentElement.classList.toggle("light", l), light);
      const colours = await page.evaluate(() => {
        const cap = document.querySelector<HTMLElement>("[data-feed-tabs-next]")!.parentElement!;
        // The surface behind the bar: the first ancestor that paints a colour.
        let el: HTMLElement | null = document.querySelector<HTMLElement>("[data-feed-filter]")!.parentElement;
        while (el && getComputedStyle(el).backgroundColor === "rgba(0, 0, 0, 0)") el = el.parentElement;
        return { cap: getComputedStyle(cap).backgroundColor, surface: el ? getComputedStyle(el).backgroundColor : "none" };
      });
      expect(colours.cap).not.toBe("rgba(0, 0, 0, 0)");
      expect(colours.cap).toBe(colours.surface);
    }
  });

  test("a selected tab scrolls clear of the left cap, after a click and after the keys", async ({ page }) => {
    await setup(page, MANY);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const next = feed(page).locator("[data-feed-tabs-next]");
    const prevButton = feed(page).locator("[data-feed-tabs-prev]");
    const leftCap = prevButton.locator("xpath=../..");
    const clear = async (tab: ReturnType<typeof tabs>) => {
      // The left cap is there while the bar is scrolled, and the tab must sit beside it.
      await expect(prevButton).toBeVisible();
      const t = (await tab.boundingBox())!;
      const c = (await leftCap.boundingBox())!;
      expect(t.x).toBeGreaterThanOrEqual(c.x + c.width - 1);
    };
    // Scroll to the end, then choose the third tab: it must not sit under the cap.
    for (let i = 0; i < 4 && (await next.count()); i++) {
      await next.click();
      await page.waitForTimeout(400);
    }
    await expect(prevButton).toBeVisible();
    const second = tabs(page).nth(2);
    await second.click();
    await page.waitForTimeout(300);
    await expect(second).toHaveAttribute("aria-selected", "true");
    await clear(second);
    // End, then ArrowLeft: the tab before the last one is clear of the right cap as well.
    await page.keyboard.press("End");
    await page.waitForTimeout(300);
    await page.keyboard.press("ArrowLeft");
    await page.waitForTimeout(300);
    const beforeLast = tabs(page).nth(7);
    await expect(beforeLast).toHaveAttribute("aria-selected", "true");
    const t = (await beforeLast.boundingBox())!;
    const bar = (await feed(page).getByRole("tablist").boundingBox())!;
    const rightCap = feed(page).locator("[data-feed-tabs-next]").locator("xpath=../..");
    if (await rightCap.count()) {
      const r = (await rightCap.boundingBox())!;
      expect(t.x + t.width).toBeLessThanOrEqual(r.x + 1);
    } else {
      expect(t.x + t.width).toBeLessThanOrEqual(bar.x + bar.width + 1);
    }
    // ArrowLeft back down to the third tab.
    for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowLeft");
    await page.waitForTimeout(300);
    await expect(second).toHaveAttribute("aria-selected", "true");
    await clear(second);
  });
});

/** Twenty-five tasks, one reply and one approval. All draws 20 rows, so a tab must not. */
const LOTS = [
  ...Array.from({ length: 25 }, (_, i) => card("tasks", `l${i}`, "overdue", `Task number ${i}`, `/projects?task=l${i}`)),
  card("email", "a", "needs_reply", "Re: the supplier contract", "/email?email=a&account=acc1"),
  card("approvals", "p1", "approvals_waiting", "3 approvals wait for you", "/approvals"),
];

test.describe("My Day, the Needs you counts keep their promise (CC-11)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a tab counts every row of its app and draws every row it counts, past the cut of 20", async ({ page }) => {
    await setup(page, LOTS);
    await page.goto("/");
    await expect(feedCard(page, "tasks:l0")).toBeVisible();
    await expect(feed(page).getByRole("tab", { name: /^My work/ })).toHaveText("My work25");
    await feed(page).getByRole("tab", { name: /^My work/ }).click();
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(25);
    await expect(feedCard(page, "tasks:l24")).toBeVisible();
  });

  test("a dismiss takes one off the count, and the active tab falls back to All when it empties", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await feed(page).getByRole("tab", { name: /^My work/ }).click();
    await expect(tabs(page).nth(1)).toHaveText("My work2");
    await feedCard(page, "tasks:t1").getByRole("button", { name: "More for Send the quote" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Dismiss" }).click();
    await expect(tabs(page).nth(1)).toHaveText("My work1");
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(1);
    await feedCard(page, "tasks:t2").getByRole("button", { name: "More for Book the venue" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Dismiss" }).click();
    await expect(feed(page).getByRole("tab", { name: /^My work/ })).toHaveCount(0);
    await expect(tabs(page).first()).toHaveAttribute("aria-selected", "true");
    await expect(tabs(page).first()).toHaveText("All");
    await expect(feedCard(page, "email:a")).toBeVisible();
  });

  test("the AI questions tab draws, and filters to the run", async ({ page }) => {
    await setup(page, { ai: true });
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const ai = tabs(page).filter({ hasText: /^AI questions/ });
    await expect(ai).toHaveText("AI questions1", { timeout: 20_000 });
    await ai.click();
    await expect(page.getByText("Which supplier do you prefer?")).toBeVisible();
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(0);
    await expect(page.getByText("Nothing needs you")).toHaveCount(0);
  });

  test("the Reminders tab draws, filters to the band, and the band drops its own heading word", async ({ page }) => {
    await setup(page, { reminders: true });
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const rem = tabs(page).filter({ hasText: /^Reminders/ });
    await expect(rem).toHaveText("Reminders2");
    // In All the band keeps its heading.
    await expect(feed(page).locator("#my-day-reminders")).toBeVisible();
    await rem.click();
    await expect(page.getByTestId("my-day-reminders")).toBeVisible();
    await expect(page.getByText("Call the bank")).toBeVisible();
    await expect(feed(page).locator("[data-feed-row]")).toHaveCount(0);
    await expect(feed(page).locator("#my-day-reminders")).toHaveClass(/sr-only/);
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

  test("a bar that overflows swipes, and no chevron shows", async ({ page }) => {
    await setup(page, MANY);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const overflows = await page.evaluate(() => {
      const bar = document.querySelector<HTMLElement>('[data-testid="my-day-feed"] [role="tablist"]')!;
      return bar.scrollWidth > bar.clientWidth + 1;
    });
    expect(overflows).toBe(true);
    await expect(page.locator("[data-feed-tabs-next], [data-feed-tabs-prev]")).toBeHidden();
  });
});
