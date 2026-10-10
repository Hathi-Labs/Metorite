import { expect, test, type Page, type Route } from "@playwright/test";

// The email search bar: scope + pills.
//
// These drive the real UI and assert the REQUEST the store builds, so they cover
// both halves of the contract — that the bar renders/behaves, and that what it
// sends matches what `/email/search` expects (scope as `folder`, tag pills as
// repeated `labels`, from:/to: as `from_addr`/`to_addr`).

const ACCOUNT = {
  id: "acc1",
  provider: "microsoft",
  email_address: "me@fracktal.in",
  label: "Work",
  avatar_color: "#6366f1",
  unread_count: 1,
  sync_enabled: true,
  sync_status: "idle",
};

const FOLDERS = [
  { provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 2, unread_count: 1 },
  { provider_folder_id: "sent", name: "Sent Items", type: "system", message_count: 1, unread_count: 0 },
];

const MSG = {
  id: "m1",
  provider_message_id: "pm1",
  thread_id: "t1",
  account_id: "acc1",
  from_address: { name: "Alice Example", email: "alice@example.com" },
  to_addresses: [{ name: "Me", email: "me@fracktal.in" }],
  cc_addresses: [],
  subject: "Quarterly report",
  body_text: "Here is the quarterly report body for review.",
  snippet: "Here is the quarterly report body for review.",
  is_read: false,
  is_starred: false,
  is_flagged: false,
  importance: "normal",
  folder: "inbox",
  has_attachments: false,
  categories: ["Newsletter", "Reply"],
  received_at: "2026-06-20T10:00:00Z",
  synced_at: "2026-06-20T10:00:00Z",
};

/** Records every /email/search + /email/messages URL the app requests. */
type Calls = { search: URL[]; messages: URL[] };

async function installMocks(page: Page, calls: Calls) {
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route(/.*\/api\/integrations\/status.*/, (r) => json(r, []));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) => json(r, FOLDERS));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) =>
    json(r, [{ name: "Newsletter", color: null }, { name: "Reply", color: null }])
  );

  await page.route(/.*\/api\/email\/search\?.*/, (r) => {
    calls.search.push(new URL(r.request().url()));
    return json(r, { emails: [MSG], total: 1, page: 1, page_size: 50, hybrid: false });
  });
  await page.route(/.*\/api\/email\/messages\?.*/, (r) => {
    calls.messages.push(new URL(r.request().url()));
    return json(r, { emails: [MSG], total: 1, page: 1, page_size: 50 });
  });
  await page.route(/.*\/api\/email\/messages\/m1(\?.*)?$/, (r) => json(r, MSG));
}

/** The most recent search request, waited for. */
async function lastSearch(calls: Calls): Promise<URL> {
  await expect.poll(() => calls.search.length).toBeGreaterThan(0);
  return calls.search[calls.search.length - 1];
}

function newCalls(): Calls {
  return { search: [], messages: [] };
}

test.describe("Email search bar", () => {
  test("sits in the top bar and defaults to searching the open folder", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    // Default scope follows the open folder → "Search Inbox".
    const input = page.getByPlaceholder("Search Inbox");
    await expect(input).toBeVisible();

    // It lives in the LIST's header row — sharing a row with the folder
    // heading, rather than down in the sidebar as it used to — and is centred
    // ON THAT ROW (not on the viewport: the row starts right of the sidebar).
    // Since 2026-10-10 the app's own title bar sits above this row, and the
    // folder is the row's h2. The app's h1 is "My Email", in the title bar.
    const geom = await page.evaluate(() => {
      const el = document.querySelector('input[placeholder="Search Inbox"]')!;
      const wrap = el.closest("div.relative")!;          // the SearchBar root
      const row = document.querySelector("[data-email-list-header]")!;
      const head = row.querySelector("h2")!;
      const c = (n: Element) => {
        const b = n.getBoundingClientRect();
        return { y: b.y, centre: b.x + b.width / 2 };
      };
      return { bar: c(wrap), row: c(row), head: c(head) };
    });
    // Same row as the heading.
    expect(Math.abs(geom.bar.y - geom.head.y)).toBeLessThan(30);
    // Centred on the bar itself.
    expect(Math.abs(geom.bar.centre - geom.row.centre)).toBeLessThan(8);
  });

  test("expanded, it never overlaps the top bar's actions", async ({ page }) => {
    // Regression: the bar widens while in use. It used to widen into the
    // fixed-size action icons on its right, which then rendered on top of it
    // and swallowed clicks meant for the filter button.
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    const input = page.getByPlaceholder("Search Inbox");
    await input.fill('invoice from:"Fracktal Finance" tag:Receipt');
    await input.press("Enter");
    await expect(page.getByText("From: Fracktal Finance")).toBeVisible();

    const bar = await page.getByLabel("Add a filter").locator("../..").boundingBox();
    const actions = await page.getByTitle("Mailbox settings").boundingBox();
    expect(bar && actions).toBeTruthy();
    // The search bar must END before the actions BEGIN — no shared pixels.
    expect(bar!.x + bar!.width).toBeLessThanOrEqual(actions!.x + 1);
    // And the filter button is genuinely clickable (not covered).
    await page.getByLabel("Add a filter").click({ timeout: 5_000 });
    await expect(page.getByRole("menu", { name: "Filter menu" })).toBeVisible();
  });

  test("typed text searches the current folder scope", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    await page.getByPlaceholder("Search Inbox").fill("quarterly");
    const url = await lastSearch(calls);
    expect(url.searchParams.get("q")).toBe("quarterly");
    expect(url.searchParams.get("folder")).toBe("inbox");
  });

  test("scope dropdown retargets the search to All folders", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    await page.getByPlaceholder("Search Inbox").fill("quarterly");
    await lastSearch(calls);

    await page.getByTitle("Choose where to search").click();
    await page.getByRole("button", { name: "All folders", exact: true }).click();

    await expect.poll(async () => (await lastSearch(calls)).searchParams.get("folder"))
      .toBe("all");
    // The bar now says where it's looking.
    await expect(page.getByText("All folders").first()).toBeVisible();
  });

  test("typed from: becomes a closable pill and a from_addr filter", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    const input = page.getByPlaceholder("Search Inbox");
    await input.fill('report from:"Alice Example"');
    await input.press("Enter");

    // Lifted out of the text into a pill…
    await expect(page.getByText("From: Alice Example")).toBeVisible();
    // …and sent as a dedicated filter, with the rest left as the query.
    await expect.poll(async () => (await lastSearch(calls)).searchParams.get("from_addr"))
      .toBe("Alice Example");
    expect((await lastSearch(calls)).searchParams.get("q")).toBe("report");

    // The pill's × removes it and re-runs the search without it.
    await page.getByLabel("Remove From: Alice Example filter").click();
    await expect(page.getByText("From: Alice Example")).toHaveCount(0);
    await expect.poll(async () => (await lastSearch(calls)).searchParams.get("from_addr"))
      .toBeNull();
  });

  test("tag pills stack as repeated labels and need no search text", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    // A pills-only search: pick two tags, type nothing. Scoped to the filter
    // menu — "Newsletter"/"Reply" also appear as category chips on the message
    // cards behind it.
    const menu = page.getByRole("menu", { name: "Filter menu" });
    await page.getByLabel("Add a filter").click();
    await menu.getByRole("button", { name: "Newsletter", exact: true }).click();
    await page.getByLabel("Add a filter").click();
    await menu.getByRole("button", { name: "Reply", exact: true }).click();

    await expect.poll(async () => (await lastSearch(calls)).searchParams.getAll("labels"))
      .toEqual(["Newsletter", "Reply"]);
    // No text typed → no q at all (a filters-only search).
    expect((await lastSearch(calls)).searchParams.get("q")).toBeNull();
    await expect(page.getByText("Tag: Newsletter")).toBeVisible();
    await expect(page.getByText("Tag: Reply")).toBeVisible();
  });

  test("is:unread is understood as a state filter", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    const input = page.getByPlaceholder("Search Inbox");
    await input.fill("is:unread");
    await input.press("Enter");

    // exact — the top bar also shows an "N unread" count badge.
    await expect(page.getByText("Unread", { exact: true })).toBeVisible();
    await expect.poll(async () => (await lastSearch(calls)).searchParams.get("is_read"))
      .toBe("false");
  });

  test("clearing the search returns to the plain folder list", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    await page.getByPlaceholder("Search Inbox").fill("quarterly");
    await lastSearch(calls);

    const before = calls.messages.length;
    await page.getByLabel("Clear search").click();
    // Back to /email/messages (the folder list), not /email/search.
    await expect.poll(() => calls.messages.length).toBeGreaterThan(before);
    await expect(page.getByPlaceholder("Search Inbox")).toHaveValue("");
  });
});

test.describe("All folder", () => {
  test("lists every folder but junk and trash", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    await page.getByRole("button", { name: /^All/ }).first().click();

    // The All view is a plain list scoped to the `all` pseudo-folder — the
    // backend expands it to "not junk, not trash".
    await expect.poll(() => {
      const u = calls.messages[calls.messages.length - 1];
      return u?.searchParams.get("folder");
    }).toBe("all");
    await expect(page.getByRole("heading", { name: "All" })).toBeVisible();
  });

  test("searching from All keeps the All scope", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    await page.getByRole("button", { name: /^All/ }).first().click();
    await page.getByPlaceholder("Search All").fill("quarterly");

    const url = await lastSearch(calls);
    expect(url.searchParams.get("folder")).toBe("all");
    expect(url.searchParams.get("q")).toBe("quarterly");
  });

  test("is not offered as a move-to destination (it's a view, not a folder)", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    // Open a message, then the toolbar's "Move to folder" picker.
    await page.getByText("Quarterly report").first().click();
    await page.getByTitle("Move to folder").click();

    const menu = page.getByText("Move to").locator("..");
    // Real folders are offered…
    await expect(menu.getByRole("button", { name: "Sent" })).toBeVisible();
    // …but the All and Starred VIEWS are not — you can't move mail into them.
    await expect(menu.getByRole("button", { name: "All", exact: true })).toHaveCount(0);
    await expect(menu.getByRole("button", { name: "Starred" })).toHaveCount(0);
  });

  test("offers no 'load older from server' on the All view", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    // Inbox (a real folder) can page the provider…
    await expect(page.getByText("Load older messages from server")).toBeVisible();
    // …but All can't (no provider folder to backfill from).
    await page.getByRole("button", { name: /^All/ }).first().click();
    await expect(page.getByRole("heading", { name: "All" })).toBeVisible();
    await expect(page.getByText("Load older messages from server")).toHaveCount(0);
  });
});

test.describe("from:/to: pills are single-valued", () => {
  test("a second from: replaces the first, so the bar never lies", async ({ page }) => {
    const calls = newCalls();
    await installMocks(page, calls);
    await page.goto("/email");

    // The search input is a textbox in the bar; its placeholder empties once a
    // pill is present, so target it by role rather than placeholder text.
    const input = page.getByRole("textbox").first();
    await input.fill("from:alice@x.com");
    await input.press("Enter");
    await expect(page.getByText("From: alice@x.com")).toBeVisible();

    await input.fill("from:bob@x.com");
    await input.press("Enter");

    // Only bob remains — the stale alice chip must be gone, and the request
    // carries bob (previously two chips showed but only the last filtered).
    await expect(page.getByText("From: alice@x.com")).toHaveCount(0);
    await expect(page.getByText("From: bob@x.com")).toBeVisible();
    await expect.poll(async () => (await lastSearch(calls)).searchParams.get("from_addr"))
      .toBe("bob@x.com");
  });
});
