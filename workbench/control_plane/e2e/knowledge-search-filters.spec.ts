import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The Knowledge Search filters and the match bar (WS-61 KB-3d,
 * `knowledge_base.md` §21.7).
 *
 * The BFF at `/api/knowledge/**` is a mock in the shape of
 * `knowledge-search.spec.ts`. It honours `space`, `author`, `updated_after`
 * and `title_only`, and answers `terms` and `author_email` as the gateway
 * does. The tests hold the acceptance lines: each chip sends its parameter,
 * a chip clears, the address keeps the filters on a reload, the snippets mark
 * the matched words, and a result opens with a match bar that counts, steps
 * and closes.
 *
 * The real gateway half is `tests/unit/test_kb_search_r8.py`.
 */

const COMPANY = "kf-company";
const OPS = "kf-ops";
const DAY = 86_400_000;

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

interface KbPage {
  id: string;
  space_id: string;
  title: string;
  body_md: string;
  author: string;
  age_days: number;
  heading: string;
  text: string;
}

const PAGES: KbPage[] = [
  {
    id: "kp-refund",
    space_id: COMPANY,
    title: "Refund policy",
    body_md: "# Refunds\n\nWe refund within thirty days of delivery.\n\nA refund needs a receipt.\n\nNo refund after sixty days.",
    author: "asha@example.com",
    age_days: 2,
    heading: "Refunds > Window",
    text: "We refund within thirty days of delivery.",
  },
  {
    id: "kp-dock",
    space_id: OPS,
    title: "Returns at the dock",
    body_md: "# Receiving\n\nLog each refund item in the returns sheet.",
    author: "ravi@example.com",
    age_days: 20,
    heading: "Receiving > Returns",
    text: "Log each refund item in the returns sheet.",
  },
  {
    id: "kp-old",
    space_id: OPS,
    title: "Old refund rules",
    body_md: "Refunds were slower before.",
    author: "ravi@example.com",
    age_days: 400,
    heading: "",
    text: "Refunds were slower before.",
  },
];

const SPACE_NAMES: Record<string, string> = { [COMPANY]: "Company", [OPS]: "Operations" };

async function mockKnowledge(page: Page) {
  const searches: URL[] = [];
  const now = Date.now();
  const stamp = (p: KbPage) => new Date(now - p.age_days * DAY).toISOString();
  const summary = (p: KbPage) => ({
    id: p.id, space_id: p.space_id, parent_id: null, position: 0, title: p.title, state: "published",
    author_email: p.author, reviewers: [], review_at: null, source_ref: null, published_at: stamp(p),
    archived_at: null, history_from: null, archived_from_state: null, updated_at: stamp(p), created_at: stamp(p),
  });
  const detail = (p: KbPage) => ({
    ...summary(p),
    body_md: p.body_md, origin: null, level: "write", can_write: true,
    versions: [{ version: 1, title: p.title, author_email: p.author, reason: "create", via: "web", created_at: stamp(p) }],
    links: [],
  });
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");

    if (path === "spaces") {
      return route.fulfill(json(200, {
        spaces: [COMPANY, OPS].map((id) => ({
          id, slug: id, name: SPACE_NAMES[id], purpose: "", icon: "", kind: id === COMPANY ? "company" : "team",
          owner_email: null, archived_at: null, created_at: new Date(now).toISOString(), level: "write",
          page_count: PAGES.filter((p) => p.space_id === id).length,
        })),
      }));
    }
    if (path === "search") {
      searches.push(url);
      const sp = url.searchParams;
      const query = (sp.get("q") ?? "").split(/\s+/).filter(Boolean).join(" ");
      if (query.length < 2) return route.fulfill(json(422, { detail: "Give 2 to 300 characters to find." }));
      const words = query.toLowerCase().split(" ");
      const after = sp.get("updated_after");
      const hits = PAGES.filter((p) => !sp.get("space") || p.space_id === sp.get("space"))
        .filter((p) => !sp.get("author") || p.author === sp.get("author"))
        .filter((p) => !after || new Date(stamp(p)) >= new Date(`${after}T00:00:00Z`))
        .filter((p) => words.every((w) => (sp.get("title_only") === "true" ? p.title : p.text).toLowerCase().includes(w)))
        .map((p, i) => ({
          page_id: p.id, space_id: p.space_id, space_name: SPACE_NAMES[p.space_id], title: p.title,
          heading_path: p.heading, text: p.text, score: 0.05 - i * 0.01, updated_at: stamp(p),
          href: `/knowledge/pages/${p.id}`, icon: null, author_email: p.author,
        }));
      return route.fulfill(json(200, { query, terms: words, hits, degraded: true }));
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
  return { searches };
}

async function setup(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member() });
  await page.setViewportSize({ width: 1440, height: 900 });
}

const filters = (page: Page) => page.getByRole("group", { name: "Filters" });
const lastSearch = (kb: { searches: URL[] }) => kb.searches[kb.searches.length - 1];
const titles = (page: Page) => page.locator("section[aria-labelledby^='knowledge-search-space-'] a");

test.describe("Knowledge search filters", () => {
  test("each chip sends its parameter, and a chip clears", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page);
    await page.goto("/knowledge/search?q=refund");
    await expect(titles(page)).toHaveCount(3, { timeout: 30_000 });
    expect(lastSearch(kb).searchParams.has("author")).toBe(false);
    expect(lastSearch(kb).searchParams.has("updated_after")).toBe(false);

    // Space.
    await filters(page).getByRole("button", { name: "Space", exact: true }).click();
    await page.getByRole("dialog", { name: "Filter by space" }).getByRole("button", { name: "Operations" }).click();
    await expect(titles(page)).toHaveCount(2, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.get("space")).toBe(OPS);
    await expect(page).toHaveURL(new RegExp(`space=${OPS}`));
    await filters(page).getByRole("button", { name: "Clear Space" }).click();
    await expect(titles(page)).toHaveCount(3, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.has("space")).toBe(false);
    await expect(page).not.toHaveURL(/space=/);

    // Creator, from the authors in the results.
    await filters(page).getByRole("button", { name: "Creator", exact: true }).click();
    const menu = page.getByRole("dialog", { name: "Filter by creator" });
    await expect(menu.getByRole("button", { name: "ravi@example.com" })).toBeVisible();
    await menu.getByRole("button", { name: "ravi@example.com" }).click();
    await expect(titles(page)).toHaveCount(2, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.get("author")).toBe("ravi@example.com");
    await filters(page).getByRole("button", { name: "Clear Creator" }).click();
    await expect(titles(page)).toHaveCount(3, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.has("author")).toBe(false);

    // Creator, typed.
    await filters(page).getByRole("button", { name: "Creator", exact: true }).click();
    await page.getByRole("textbox", { name: "Creator email" }).fill("asha@example.com");
    await page.keyboard.press("Enter");
    await expect(titles(page)).toHaveCount(1, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.get("author")).toBe("asha@example.com");
    await filters(page).getByRole("button", { name: "Clear Creator" }).click();

    // Date.
    await filters(page).getByRole("button", { name: "Date", exact: true }).click();
    await page.getByRole("dialog", { name: "Filter by date" }).getByRole("button", { name: "Past month" }).click();
    await expect(titles(page)).toHaveCount(2, { timeout: 15_000 });
    const after = lastSearch(kb).searchParams.get("updated_after") ?? "";
    expect(after).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const days = (Date.now() - new Date(`${after}T00:00:00Z`).getTime()) / DAY;
    expect(days).toBeGreaterThan(29);
    expect(days).toBeLessThan(32);
    await filters(page).getByRole("button", { name: "Clear Date" }).click();
    await expect(titles(page)).toHaveCount(3, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.has("updated_after")).toBe(false);

    // Title only.
    await filters(page).getByRole("button", { name: "Title only" }).click();
    await expect(titles(page)).toHaveCount(2, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.get("title_only")).toBe("true");
    await expect(page).toHaveURL(/title=1/);
    await filters(page).getByRole("button", { name: "Title only" }).click();
    await expect(titles(page)).toHaveCount(3, { timeout: 15_000 });
    expect(lastSearch(kb).searchParams.has("title_only")).toBe(false);
    await expect(page).not.toHaveURL(/title=/);
  });

  test("the address keeps the filters on a reload", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page);
    await page.goto("/knowledge/search?q=refund&author=asha%40example.com&date=year&title=1");
    await expect(titles(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(titles(page).first()).toContainText("Refund policy");
    const f = filters(page);
    await expect(f.getByRole("button", { name: "Creator: asha@example.com" })).toBeVisible();
    await expect(f.getByRole("button", { name: "Date: Past year" })).toBeVisible();
    await expect(f.getByRole("button", { name: "Title only" })).toHaveAttribute("aria-pressed", "true");
    const sent = lastSearch(kb).searchParams;
    expect(sent.get("author")).toBe("asha@example.com");
    expect(sent.get("title_only")).toBe("true");
    expect(sent.has("updated_after")).toBe(true);

    await page.reload();
    await expect(titles(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(f.getByRole("button", { name: "Creator: asha@example.com" })).toBeVisible();
    await expect(page).toHaveURL(/author=asha%40example\.com&date=year&title=1/);
  });

  test("a snippet marks the matched words, and a hit opens the page with them", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/search?q=thirty%20refund");
    const company = page.getByRole("region", { name: "Company" });
    await expect(company).toBeVisible({ timeout: 30_000 });
    const marks = company.locator("mark");
    await expect(marks.filter({ hasText: /^refund$/i }).first()).toBeVisible();
    await expect(marks.filter({ hasText: /^thirty$/i })).toBeVisible();
    // The title is marked too.
    await expect(company.getByRole("link", { name: /Refund policy/ }).locator("mark").first()).toHaveText(/refund/i);
    const href = await company.getByRole("link", { name: /Refund policy/ }).getAttribute("href");
    expect(href).toBe("/knowledge/pages/kp-refund?q=thirty%20refund");
  });
});

test.describe("the match bar", () => {
  test("a result opens with a bar that counts, steps and closes", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/search?q=refund");
    const company = page.getByRole("region", { name: "Company" });
    await expect(company).toBeVisible({ timeout: 30_000 });
    await company.getByRole("link", { name: /Refund policy/ }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-refund\?q=refund$/, { timeout: 30_000 });

    const bar = page.getByRole("search", { name: "Matches in this page" });
    // The heading "Refunds" holds the word too: 4 matches in all.
    await expect(bar.locator("[data-match-summary]")).toContainText('4 matches for "refund"', { timeout: 30_000 });
    await expect(page.locator(".kb-editor .kb-find-match")).toHaveCount(4);
    await expect(bar).toContainText("1 of 4");
    await expect(page.locator(".kb-editor .kb-find-match-current")).toHaveCount(1);

    await bar.getByRole("button", { name: "Next match" }).click();
    await expect(bar).toContainText("2 of 4");
    await bar.getByRole("button", { name: "Previous match" }).click();
    await expect(bar).toContainText("1 of 4");

    await bar.getByRole("button", { name: "Close matches" }).click();
    await expect(bar).toHaveCount(0);
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-refund$/);
    await expect(page.locator(".kb-editor .kb-find-match")).toHaveCount(0);
  });

  test("a page opened with no words has no bar", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page);
    await page.goto("/knowledge/pages/kp-refund");
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("search", { name: "Matches in this page" })).toHaveCount(0);
  });
});
