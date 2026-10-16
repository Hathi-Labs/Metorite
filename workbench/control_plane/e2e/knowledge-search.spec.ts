import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The Knowledge Search view (WS-61 KB-3b, `knowledge_base.md` §17).
 *
 * The BFF at `/api/knowledge/**` is a mock in the shape of
 * `knowledge-pages.spec.ts`. Its search finds a word in the text of a
 * published page and answers in the gateway's shape (`kb_index/query.py`
 * `_hit`). The tests hold the acceptance line: a member finds a published
 * page by a word in its body, sees it under its space with the heading path,
 * and the hit opens the page at `/knowledge/pages/<id>`. They also hold the
 * address (`?q=`), the 2-character floor and the leave guard.
 *
 * The real gateway half is `tests/unit/test_kb_search_r8.py`.
 */

const COMPANY = "ks-company";
const OPS = "ks-ops";

function member() {
  return {
    authenticated: true,
    email: "asha@example.com",
    name: "Asha Rao",
    is_admin: false,
    features: ["knowledge"],
    permissions: [],
    capabilities: [],
    roles: ["member"],
    organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
  };
}

type Chunk = { heading_path: string; text: string };
type KbPage = {
  id: string;
  space_id: string;
  title: string;
  body_md: string;
  state: "draft" | "published";
  chunks: Chunk[];
};

const PAGES: KbPage[] = [
  {
    id: "kp-refund",
    space_id: COMPANY,
    title: "Refund policy",
    body_md: "# Refunds\n\n## Window\n\nWe refund within thirty days of delivery.",
    state: "published",
    chunks: [{ heading_path: "Refunds > Window", text: "We refund within thirty days of delivery." }],
  },
  {
    id: "kp-dock",
    space_id: OPS,
    title: "Returns at the dock",
    body_md: "# Receiving\n\n## Returns\n\nLog each refund item in the returns sheet.",
    state: "published",
    chunks: [{ heading_path: "Receiving > Returns", text: "Log each refund item in the returns sheet." }],
  },
  {
    id: "kp-draft",
    space_id: COMPANY,
    title: "Refund draft",
    body_md: "A refund draft that nobody published.",
    state: "draft",
    chunks: [{ heading_path: "", text: "A refund draft that nobody published." }],
  },
];

const SPACE_NAMES: Record<string, string> = { [COMPANY]: "Company", [OPS]: "Operations" };

/** A mock of the Knowledge BFF. Returns the log of each search request. */
async function mockKnowledge(page: Page) {
  const searches: URL[] = [];
  const writes: string[] = [];
  const now = new Date().toISOString();
  const summary = (p: KbPage) => ({
    id: p.id,
    space_id: p.space_id,
    parent_id: null,
    position: 0,
    title: p.title,
    state: p.state,
    author_email: "asha@example.com",
    reviewers: [],
    review_at: null,
    source_ref: null,
    published_at: p.state === "published" ? now : null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: now,
    created_at: now,
  });
  const detail = (p: KbPage) => ({
    ...summary(p),
    body_md: p.body_md,
    origin: null,
    level: "write",
    can_write: true,
    versions: [{ version: 1, title: p.title, author_email: "asha@example.com", reason: "create", via: "web", created_at: now }],
    links: [],
  });
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    if (req.method() !== "GET") writes.push(`${req.method()} ${path}`);

    if (path === "spaces") {
      const count = (id: string) => PAGES.filter((p) => p.space_id === id).length;
      return route.fulfill(
        json(200, {
          spaces: [COMPANY, OPS].map((id) => ({
            id, slug: id, name: SPACE_NAMES[id], purpose: "", icon: "", kind: id === COMPANY ? "company" : "team",
            owner_email: null, archived_at: null, created_at: now, level: "write", page_count: count(id),
          })),
        }),
      );
    }
    if (path === "search") {
      searches.push(url);
      const query = (url.searchParams.get("q") ?? "").split(/\s+/).filter(Boolean).join(" ");
      if (query.length < 2) return route.fulfill(json(422, { detail: "Give 2 to 300 characters to find." }));
      const words = query.toLowerCase().split(" ");
      const hits = PAGES.filter((p) => p.state === "published").flatMap((p, i) =>
        p.chunks
          .filter((c) => words.every((w) => c.text.toLowerCase().includes(w)))
          .map((c) => ({
            page_id: p.id, space_id: p.space_id, space_name: SPACE_NAMES[p.space_id], title: p.title,
            heading_path: c.heading_path, text: c.text, score: 0.05 - i * 0.01, updated_at: now,
            href: `/knowledge/pages/${p.id}`,
          })),
      );
      return route.fulfill(json(200, { query, hits, degraded: true }));
    }
    if (path === "pages") {
      const space = url.searchParams.get("space");
      return route.fulfill(json(200, { pages: PAGES.filter((p) => p.space_id === space).map(summary) }));
    }
    const m = /^pages\/([^/]+)$/.exec(path);
    const found = m ? PAGES.find((p) => p.id === decodeURIComponent(m[1])) : undefined;
    if (found && req.method() === "GET") return route.fulfill(json(200, detail(found)));
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { searches, writes };
}

async function setup(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member() });
  await page.setViewportSize({ width: 1440, height: 900 });
}

const searchBox = (page: Page) => page.getByRole("searchbox", { name: "Find in Knowledge" });

test.describe("Knowledge search", () => {
  test("a word in a page body finds it under its space, and the hit opens the page", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page);

    // The rail's Search row opens the view, and the box has the focus.
    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { level: 2, name: "Spaces" })).toBeVisible({ timeout: 30_000 });
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });
    await rail.getByRole("button", { name: "Search", exact: true }).click();
    // The first visit compiles the route on the dev server.
    await expect(page).toHaveURL(/\/knowledge\/search$/, { timeout: 30_000 });
    const box = searchBox(page);
    await expect(box).toBeFocused({ timeout: 30_000 });

    await box.pressSequentially("thirty");
    const company = page.getByRole("region", { name: "Company" });
    await expect(company).toBeVisible({ timeout: 15_000 });
    await expect(company.getByRole("link", { name: /Refund policy/ })).toBeVisible();
    await expect(company.getByText("Refunds > Window")).toBeVisible();
    await expect(company.locator("mark", { hasText: "thirty" })).toBeVisible();
    // The draft holds the word too, and never shows.
    await expect(page.getByText("Refund draft")).toHaveCount(0);
    await expect(page).toHaveURL(/\/knowledge\/search\?q=thirty$/);

    // One request for the words, after the wait: k=20, no hybrid, no space.
    const last = kb.searches[kb.searches.length - 1];
    expect(last.searchParams.get("q")).toBe("thirty");
    expect(last.searchParams.get("k")).toBe("20");
    expect(last.searchParams.has("hybrid")).toBe(false);
    expect(last.searchParams.has("space")).toBe(false);

    await company.getByRole("link", { name: /Refund policy/ }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-refund$/, { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible({ timeout: 30_000 });
    expect(kb.writes).toEqual([]);
  });

  test("the hits group by space in the order of their best score", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/search?q=refund");
    const groups = page.locator("section[aria-labelledby^='knowledge-search-space-'] h3");
    await expect(groups).toHaveText(["Company", "Operations"], { timeout: 30_000 });
    await expect(page.getByRole("region", { name: "Operations" }).getByText("Receiving > Returns")).toBeVisible();
    await expect(page.locator("#knowledge-search-status")).toHaveText("2 pages in 2 spaces");
  });

  test("a reload of ?q= shows the same hits", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/search");
    await searchBox(page).fill("refund item");
    await expect(page).toHaveURL(/\/knowledge\/search\?q=refund%20item$/, { timeout: 30_000 });
    await expect(page.getByRole("link", { name: /Returns at the dock/ })).toBeVisible({ timeout: 15_000 });

    await page.reload();
    await expect(searchBox(page)).toHaveValue("refund item", { timeout: 30_000 });
    await expect(page.getByRole("link", { name: /Returns at the dock/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("link", { name: /Refund policy/ })).toHaveCount(0);
  });

  test("one character sends no request, and says why", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page);
    await page.goto("/knowledge/search");
    await searchBox(page).fill("r");
    await expect(page.locator("#knowledge-search-status")).toHaveText("Type 2 or more characters to search.", { timeout: 30_000 });
    await page.waitForTimeout(900);
    expect(kb.searches).toEqual([]);
  });

  test("no match says so, and offers a way out", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/search?q=zebra");
    await expect(page.getByText("No published page matches “zebra”.")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Clear the search" }).click();
    await expect(searchBox(page)).toHaveValue("");
    await expect(searchBox(page)).toBeFocused();
    await expect(page).toHaveURL(/\/knowledge\/search$/);
  });
});

test.describe("a search never loses an edit in silence", () => {
  test("the Search rail row and the bar's Search tool ask first", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page);
    await page.goto("/knowledge/pages/kp-refund");
    await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
    const body = page.getByRole("textbox", { name: "Page body in Markdown" });
    await body.fill("Typed text");

    const ask = page.getByRole("dialog", { name: "Discard your changes?" });
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });
    for (const open of [
      () => rail.getByRole("button", { name: "Search", exact: true }).click(),
      () => page.getByRole("button", { name: "Search Knowledge" }).click(),
    ]) {
      await open();
      await expect(ask).toBeVisible();
      await ask.locator("[data-cancel]").filter({ hasText: "Keep editing" }).click();
      await expect(ask).toHaveCount(0);
      await expect(body).toHaveValue("Typed text");
      await expect(page).toHaveURL(/\/knowledge\/pages\/kp-refund$/);
    }

    // Discard leaves for the Search view, and writes nothing.
    await rail.getByRole("button", { name: "Search", exact: true }).click();
    await ask.getByRole("button", { name: "Discard" }).click();
    await expect(page).toHaveURL(/\/knowledge\/search$/);
    await expect(searchBox(page)).toBeVisible();
    expect(kb.writes).toEqual([]);
  });
});
