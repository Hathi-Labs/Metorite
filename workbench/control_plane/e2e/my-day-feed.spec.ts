import { expect, test, type Page, type Request, type Route } from "@playwright/test";

/**
 * WS-58 MD-2b, the life of a My Day card in a browser (`my_day_feed.md` §6.2,
 * §6.8 and the MD-2b ticket; `src/lib/shell/feedState.ts`).
 *
 * The flags are build-time values, so a dev build lets one browser turn them
 * on for itself through `localStorage`, as `e2e/shell-bell.spec.ts` does.
 *
 * Each test is written so the obvious wrong build fails it:
 *   1. The seen observer posts the body of §6.8, once the 10 s tick comes, and
 *      never a card with no `state_fp`.
 *   2. `pagehide` sends the waiting sightings at once, before the tick.
 *   3. A menu act posts its item, hides the card, and leaves the bell's count
 *      and the sidebar's count unchanged. Undo posts `restore`, and the card
 *      comes back.
 *   4. A slow feed holds the bell's needs read for 4 seconds at most. Then
 *      the read goes to `/api/shell/needs`, before the feed answers.
 *   5. The order holds during a visit (§7 rule 4, fix round 1): a second
 *      answer in a new order, with one new card, moves nothing on screen.
 *      The pill says "1 new", and a click takes the server's order in.
 *      Mutation, run by hand (2026-10-14): start a new visit on each answer,
 *      and this test fails.
 *   6. Handled (MD-2c): the group draws folded after the cards, as "Handled
 *      today" and its count, and it opens. The summary counts the feed's
 *      cards, not the bell's count, and says "The rest is handled." A snooze
 *      takes one off the summary and none off the bell.
 */

const SESSION = {
  user: { email: "asha@example.com", name: "Asha Rao" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

/** Every app MEMBER may star, so every sidebar row shows. */
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();

/** Two task cards that the client may write, and one email card from an older gateway (no `state_fp`). */
const CARDS = [
  {
    signal_id: "tasks:t1",
    app: "tasks",
    kind: "overdue",
    folded: [],
    group: "needs_you",
    score: 80,
    title: "Send the quote",
    why: "Due yesterday. 1 day late.",
    at: hoursAgo(30),
    href: "/projects?task=t1",
    acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "t1" }],
    state: "new",
    updated: false,
    still_open: false,
    visits: 0,
    state_fp: "a41f09c2",
  },
  {
    signal_id: "tasks:t2",
    app: "tasks",
    kind: "overdue",
    folded: [],
    group: "needs_you",
    score: 70,
    title: "Book the venue",
    why: "Due yesterday. 1 day late.",
    at: hoursAgo(28),
    href: "/projects?task=t2",
    acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "t2" }],
    state: "seen",
    updated: true,
    still_open: false,
    visits: 1,
    state_fp: "b52f1ad3",
  },
  {
    signal_id: "email:acc1:a",
    app: "email",
    kind: "needs_reply",
    folded: [],
    group: "needs_you",
    score: 60,
    title: "Re: the supplier contract",
    why: "Ravi wrote 5 hours ago.",
    at: hoursAgo(5),
    href: "/email?email=a&account=acc1",
    acts: [],
  },
];

const needsRow = (id: string, title: string, at: string) => ({
  id: `tasks:${id}`,
  app: "tasks",
  kind: "overdue",
  title,
  detail: null,
  href: `/projects?task=${id}`,
  at,
  act: "done",
  act_ref: id,
});

/** The needs view: two tasks and one reply. A snoozed card still counts here (§6.3). */
const NEEDS = {
  count: 3,
  total: 3,
  items: [
    needsRow("t1", "Send the quote", hoursAgo(30)),
    needsRow("t2", "Book the venue", hoursAgo(28)),
    {
      id: "email:acc1:a",
      app: "email",
      kind: "needs_reply",
      title: "Re: the supplier contract",
      detail: "Ravi Kumar",
      href: "/email?email=a&account=acc1",
      at: hoursAgo(5),
      act: null,
      act_ref: null,
    },
  ],
  sources: { tasks: "ok", projects: "ok", email: "ok" },
  totals: { tasks: 2, projects: 0, email: 1 },
  capped: [],
};

interface Store {
  /** Each POST body to the state route, with the time it came. */
  posts: { items: Record<string, unknown>[]; at: number }[];
  /** The cards the server hides now: a snooze or a dismiss, until `restore`. */
  hidden: Set<string>;
  /** The time each read began. */
  feedAt: number[];
  needsAt: number[];
  /** How long the feed route waits before it answers. */
  feedDelayMs: number;
  /** While true, the server's order changes and one card is new. The test turns it on. */
  reorder: boolean;
  /** The Handled fields of the feed answer (MD-2c). Absent: an older gateway. */
  handled?: { handled: Record<string, unknown>[]; handled_ok: boolean };
  /** The needs view of the feed answer, which the bell reads. */
  needs: typeof NEEDS;
}

/** The new card of the second read, in test 5. */
const NEW_CARD = {
  ...CARDS[0],
  signal_id: "tasks:t3",
  title: "Approve the purchase order",
  href: "/projects?task=t3",
  acts: [{ id: "done", label: "Done", class: "A", way: "tap", act: "done", act_ref: "t3" }],
  state_fp: "c63f2be4",
};

async function setup(page: Page, opts: { feedDelayMs?: number } = {}): Promise<Store> {
  const store: Store = {
    posts: [],
    hidden: new Set(),
    feedAt: [],
    needsAt: [],
    feedDelayMs: opts.feedDelayMs ?? 0,
    reorder: false,
    needs: NEEDS,
  };
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-dock", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.setItem("cc-my-day-feed", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  });
  // One router, as `sidebar-row-counts.spec.ts` has it.
  await page.route("**/api/**", async (route: Route) => {
    const req: Request = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/shell/feed/state") {
      const body = req.postDataJSON() as { items: Record<string, unknown>[] };
      store.posts.push({ items: body.items, at: Date.now() });
      for (const item of body.items) {
        const id = String(item.signal_id);
        if (["snooze", "dismiss", "not_useful"].includes(String(item.action))) store.hidden.add(id);
        if (item.action === "restore") store.hidden.delete(id);
      }
      return json({ written: body.items.length });
    }
    if (path === "/api/shell/feed") {
      store.feedAt.push(Date.now());
      if (store.feedDelayMs > 0) await new Promise((r) => setTimeout(r, store.feedDelayMs));
      const all = store.reorder ? [CARDS[2], NEW_CARD, CARDS[1], CARDS[0]] : CARDS;
      const cards = all.filter((c) => !store.hidden.has(c.signal_id));
      return json({
        cards,
        groups: cards.length ? [{ id: "needs_you", count: cards.length }] : [],
        needs: store.needs,
        sources: { tasks: "ok", projects: "ok", email: "ok", approvals: "absent", calendar: "ok" },
        capped: [],
        ...(store.handled ?? {}),
      });
    }
    if (path === "/api/shell/needs") {
      store.needsAt.push(Date.now());
      return json(store.needs);
    }
    if (path === "/api/auth/me/shell") return json(LAYOUT);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/projects/my/inbox") return json({ rows: [], total: 0 });
    if (path.startsWith("/api/projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (path === "/api/chat/active-sessions") return json([]);
    if (/^\/api\/(notes|chat|agent|apps|agents|email\/accounts)\b/.test(path)) return json([]);
    return json({ rows: [], items: [], total: 0 });
  });
  return store;
}

const feed = (page: Page) => page.getByTestId("my-day-feed");
const feedCard = (page: Page, id: string) => page.getByTestId(`feed-card-${id}`);
const bell = (page: Page) => page.locator("[data-shell-bar] [data-shell-bell]");
const sidebar = (page: Page) => page.locator("aside[data-collapsed]");

/** The seen items of the posts, by card. */
const seenIds = (store: Store) =>
  store.posts.flatMap((p) => p.items.filter((i) => i.action === "seen").map((i) => String(i.signal_id))).sort();

test.describe("My Day feed, the life of a card (MD-2b)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the seen observer posts the body of §6.8 on the 10 s tick, and never a card with no state_fp", async ({ page }) => {
    test.setTimeout(60_000);
    const store = await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const shownAt = Date.now();
    // The new card wears its dot, the updated one says so.
    await expect(feedCard(page, "tasks:t1").locator('[aria-label="New"]')).toBeVisible();
    await expect(feedCard(page, "tasks:t2")).toContainText("Updated");
    await expect.poll(() => store.posts.length, { timeout: 20_000 }).toBeGreaterThan(0);
    // One POST, on the tick, and not before.
    expect(store.posts[0].at - shownAt).toBeGreaterThan(7_000);
    expect(store.posts[0].items.sort((a, b) => String(a.signal_id).localeCompare(String(b.signal_id)))).toEqual([
      { signal_id: "tasks:t1", state_fp: "a41f09c2", kind: "overdue", action: "seen", until: null },
      { signal_id: "tasks:t2", state_fp: "b52f1ad3", kind: "overdue", action: "seen", until: null },
    ]);
    // The email card has no state_fp: no sighting, and no menu.
    expect(seenIds(store)).not.toContain("email:acc1:a");
    await expect(feedCard(page, "email:acc1:a").getByRole("button", { name: /^More for/ })).toHaveCount(0);
  });

  test("pagehide sends the sightings at once, before the tick", async ({ page }) => {
    const store = await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    // Past the 1.5 s dwell, well before the 10 s tick.
    await page.waitForTimeout(2_500);
    expect(store.posts).toEqual([]);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false })));
    await expect.poll(() => seenIds(store), { timeout: 5_000 }).toEqual(["tasks:t1", "tasks:t2"]);
    expect(store.posts).toHaveLength(1);
  });

  test("a menu act posts its item and hides the card, the bell and the sidebar keep their counts, and Undo restores it", async ({
    page,
  }) => {
    const store = await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 3 items");
    // My Tasks has no row since OP-4 (2026-10-25): its count is on Projects.
    const tasksRow = sidebar(page).getByRole("link", { name: "Projects, 2 need you", exact: true });
    await expect(tasksRow).toBeVisible();

    await feedCard(page, "tasks:t1").getByRole("button", { name: "More for Send the quote" }).click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem")).toHaveText([
      "Later today",
      "Tomorrow",
      "Next week",
      "Until it changes",
      "Dismiss",
      "Not useful",
    ]);
    await menu.getByRole("menuitem", { name: "Tomorrow" }).click();

    await expect(feedCard(page, "tasks:t1")).toHaveCount(0);
    const snooze = store.posts.find((p) => p.items.some((i) => i.action === "snooze"));
    expect(snooze?.items).toEqual([
      { signal_id: "tasks:t1", state_fp: "a41f09c2", kind: "overdue", action: "snooze", until: "tomorrow" },
    ]);
    // §6.3: snooze never reaches the bell or the sidebar.
    await expect(page.getByText("Snoozed until tomorrow")).toBeVisible();
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 3 items");
    await expect(tasksRow).toBeVisible();
    // The feed read again after the write, and the card stays away.
    await expect.poll(() => store.feedAt.length).toBeGreaterThan(1);
    await expect(feedCard(page, "tasks:t1")).toHaveCount(0);

    // A REAL click on the shared toast's Undo.
    await page.getByRole("button", { name: "Undo" }).click({ timeout: 5_000 });
    await expect.poll(() => store.posts.flatMap((p) => p.items).filter((i) => i.action === "restore")).toEqual([
      { signal_id: "tasks:t1", state_fp: "a41f09c2", kind: "overdue", action: "restore", until: null },
    ]);
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 3 items");
  });

  test("a slow feed holds the bell's needs read for 4 seconds, then the needs route answers", async ({ page }) => {
    test.setTimeout(60_000);
    const store = await setup(page, { feedDelayMs: 7_500 });
    await page.goto("/");
    await expect.poll(() => store.feedAt.length, { timeout: 20_000 }).toBeGreaterThan(0);
    // The bell counts from the needs route, while the feed still waits.
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 3 items", { timeout: 7_000 });
    expect(store.needsAt.length).toBeGreaterThan(0);
    const wait = store.needsAt[0] - store.feedAt[0];
    expect(wait).toBeGreaterThan(3_500);
    expect(wait).toBeLessThan(7_000);
    await expect(feed(page)).toBeVisible();
  });

  test("the order holds during a visit, a new card waits behind the pill, and a click takes it in", async ({ page }) => {
    test.setTimeout(60_000);
    const store = await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const order = () =>
      feed(page)
        .locator('[data-testid^="feed-card-"]')
        .evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.testid));
    const first = ["feed-card-tasks:t1", "feed-card-tasks:t2", "feed-card-email:acc1:a"];
    expect(await order()).toEqual(first);
    // The server now answers in a new order, with one new card. Past the
    // cache's fresh window, a return to the tab reads the feed again.
    store.reorder = true;
    const reads = store.feedAt.length;
    await page.waitForTimeout(5_500);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => store.feedAt.length).toBeGreaterThan(reads);
    const pill = feed(page).getByRole("button", { name: "1 new" });
    await expect(pill).toBeVisible();
    // Nothing moved under the pointer, and the new card waits.
    expect(await order()).toEqual(first);
    await expect(feedCard(page, "tasks:t3")).toHaveCount(0);
    await pill.click();
    await expect(pill).toHaveCount(0);
    expect(await order()).toEqual([
      "feed-card-email:acc1:a",
      "feed-card-tasks:t3",
      "feed-card-tasks:t2",
      "feed-card-tasks:t1",
    ]);
  });

  test("Handled draws folded and opens, and the summary counts the feed and makes the claim (MD-2c)", async ({ page }) => {
    const store = await setup(page);
    // The bell's view counts 5: a snoozed card still counts there (§6.3).
    store.needs = { ...NEEDS, count: 5, total: 5 };
    store.handled = {
      handled: [
        { app: "tasks", title: "Send the invoice", href: "/tasks?task=h1", by: { kind: "you", name: null }, at: hoursAgo(1) },
        { app: "email", title: "Receipts from the courier", href: "/email", by: { kind: "rules", name: null }, at: hoursAgo(2) },
      ],
      handled_ok: true,
    };
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    const summary = page.getByTestId("my-day-summary");
    await expect(summary).toHaveText("3 things need you. The rest is handled.");
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 5 items");

    // Folded, after the cards, with its count.
    const fold = feed(page).getByRole("button", { name: "Handled today 2" });
    await expect(fold).toHaveAttribute("aria-expanded", "false");
    await expect(feed(page).getByText("Send the invoice")).toHaveCount(0);
    const below = await page.evaluate(() => {
      const group = document.querySelector('[data-feed-group="handled"]');
      const cards = document.querySelectorAll('[data-testid^="feed-card-"]');
      const last = cards[cards.length - 1];
      return !!group && !!last && !!(last.compareDocumentPosition(group) & Node.DOCUMENT_POSITION_FOLLOWING);
    });
    expect(below).toBe(true);

    // It opens: each row names the entity as a link, and who closed it.
    await fold.click();
    await expect(fold).toHaveAttribute("aria-expanded", "true");
    const rows = feed(page).locator("[data-feed-handled-row]");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toHaveAttribute("href", "/tasks?task=h1");
    await expect(rows.nth(0)).toContainText("Send the invoice");
    await expect(rows.nth(0).locator("[data-feed-handled-by]")).toContainText(/^You · /);
    await expect(rows.nth(1).locator("[data-feed-handled-by]")).toContainText(/^Email rules · /);
    // Handled is no card: no sighting mark, no menu.
    await expect(feed(page).locator('[data-feed-group="handled"] [data-feed-seen]')).toHaveCount(0);

    // A snooze takes the card off the summary, and the bell keeps its count.
    await feedCard(page, "tasks:t1").getByRole("button", { name: "More for Send the quote" }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: "Tomorrow" }).click();
    await expect(feedCard(page, "tasks:t1")).toHaveCount(0);
    await expect(summary).toHaveText("2 things need you. The rest is handled.");
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 5 items");
  });

  test("an older gateway gives no Handled group and no claim (MD-2c)", async ({ page }) => {
    await setup(page);
    await page.goto("/");
    await expect(feedCard(page, "tasks:t1")).toBeVisible();
    await expect(page.getByTestId("my-day-summary")).toHaveText("3 things need you.");
    await expect(feed(page).locator('[data-feed-group="handled"]')).toHaveCount(0);
  });
});
