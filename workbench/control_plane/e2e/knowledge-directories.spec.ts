import { expect, test, type Page } from "@playwright/test";

import { SPACE_ID, busyPages, setupHome } from "./knowledgeHomeMock";

/**
 * The directories of Knowledge (WS-61 KC-8b, `knowledge_curation.md` §6).
 *
 * The BFF is the stateful mock of `knowledgeHomeMock.ts`, and this file adds
 * the entity routes after it, so they answer first. The rail draws one row
 * for each type that `entity-types` answers. A directory lists one type and
 * searches. "Write the page" on a row with no entity calls by-ref first, and
 * then the page route, and opens the draft in the editor. The warning, the
 * space choice and the two refusals (404 and 409) are read here too.
 *
 * The real gateway half is `tests/unit/test_kc_entities_r8.py`.
 */

type MockEntity = {
  id: string | null;
  type: string;
  ref_table: string;
  ref_id: string;
  name: string;
  aliases: string[];
  space_id: string | null;
  page_id: string | null;
};

const customer = (ref_id: string, name: string, extra: Partial<MockEntity> = {}): MockEntity => ({
  id: null, type: "customer", ref_table: "crm_companies", ref_id, name, aliases: [], space_id: null, page_id: null, ...extra,
});

const CUSTOMERS: MockEntity[] = [
  customer("c-1", "Penrose Works"),
  customer("c-2", "Pendle Labs", { id: "e-2", page_id: "kp-9" }),
  customer("c-3", "Acme Tools"),
];

interface Call {
  method: string;
  path: string;
  body: unknown;
}

interface Opts {
  types?: string[] | "404";
  customers?: MockEntity[];
  truncated?: boolean;
  byRef?: "ok" | 404;
  pageAnswer?: 201 | 404 | 409 | 422;
}

const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function setupDirectories(page: Page, opts: Opts = {}) {
  await setupHome(page, { level: "write", pages: busyPages() });
  const calls: Call[] = [];
  const now = new Date().toISOString();

  // The spaces again, with a `me` space the dialog must never offer.
  await page.route("**/api/knowledge/spaces", (route) => {
    const space = (id: string, slug: string, name: string, kind: string, level: string) => ({
      id, slug, name, purpose: "", icon: "", kind, owner_email: null, archived_at: null, created_at: now, level, page_count: 0,
    });
    return route.fulfill(json(200, {
      spaces: [
        space(SPACE_ID, "company", "Company", "company", "write"),
        space("ks-ops", "team-ops", "Operations", "team", "write"),
        space("ks-me", "me-asha", "My notes", "me", "manage"),
      ],
    }));
  });

  await page.route("**/api/knowledge/entity-types", (route) => {
    if (opts.types === "404") return route.fulfill(json(404, { detail: "Not Found" }));
    return route.fulfill(json(200, { types: opts.types ?? ["company", "customer"] }));
  });

  await page.route("**/api/knowledge/entities**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    if (req.method() !== "GET") calls.push({ method: req.method(), path, body });
    if (path === "entities" && req.method() === "GET") {
      const type = url.searchParams.get("type");
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      calls.push({ method: "GET", path: `entities?${url.searchParams.toString()}`, body: null });
      const all = type === "customer" ? (opts.customers ?? CUSTOMERS) : [];
      const rows = all.filter((e) => e.name.toLowerCase().includes(q));
      return route.fulfill(json(200, { entities: rows, truncated: opts.truncated ?? false }));
    }
    if (path === "entities/by-ref") {
      if (opts.byRef === 404) return route.fulfill(json(404, { detail: "Not found" }));
      const row = CUSTOMERS.find((e) => e.ref_id === body.ref_id);
      return route.fulfill(json(200, { ...row, id: `e-${body.ref_id}` }));
    }
    if (/^entities\/[^/]+\/page$/.test(path)) {
      const status = opts.pageAnswer ?? 201;
      if (status !== 201) return route.fulfill(json(status, { detail: status === 422 ? "The server refused the space." : "No" }));
      return route.fulfill(json(201, { entity: { id: "e-x" }, page: { id: "kp-new-1" } }));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { calls };
}

const rail = (page: Page) => page.getByRole("navigation", { name: "Knowledge spaces" });
/** A Directories row. The Company space has a row of its own, so the type is the handle. */
const dirRow = (page: Page, type: string) => rail(page).locator(`[data-directory-type="${type}"]`).getByRole("button");
const writeButton = (page: Page, name: string) => page.getByRole("button", { name: `Write the page for ${name}` });

test.describe("Knowledge directories", () => {
  test("the rail draws the types it is given, and a click opens the directory", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setupDirectories(page);

    await page.goto("/knowledge");
    await expect(dirRow(page, "customer")).toBeVisible({ timeout: 60_000 });
    await expect(dirRow(page, "company")).toBeVisible();
    await expect(rail(page).locator("[data-directory-type]")).toHaveCount(2);
    await expect(rail(page).getByText("Directories")).toBeVisible();
    await dirRow(page, "customer").click();
    await expect(page).toHaveURL(/\/knowledge\/directories\/customer$/, { timeout: 60_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Customers" })).toBeVisible({ timeout: 60_000 });
    await expect(page.locator('[data-entity-ref="c-1"]')).toContainText("Penrose Works");
    await expect(page.locator("[data-entity-ref]")).toHaveCount(3);
    await expect(dirRow(page, "customer")).toHaveAttribute("aria-current", "page");
    // One bar, one h1.
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  });

  test("a search narrows the list, and a long list says so", async ({ page }) => {
    test.setTimeout(120_000);
    const mock = await setupDirectories(page, { truncated: true });
    await page.goto("/knowledge/directories/customer");
    await expect(page.locator("[data-entity-ref]")).toHaveCount(3, { timeout: 60_000 });
    await expect(page.getByText("Showing the first 3. Search to narrow the list.")).toBeVisible();

    await page.getByRole("searchbox", { name: "Find in Customers" }).fill("pen");
    await expect(page.locator("[data-entity-ref]")).toHaveCount(2, { timeout: 15_000 });
    await expect(page).toHaveURL(/q=pen$/);
    expect(mock.calls.some((c) => c.path.includes("q=pen"))).toBe(true);

    await page.getByRole("searchbox", { name: "Find in Customers" }).fill("zzz");
    await expect(page.getByText("No match")).toBeVisible({ timeout: 15_000 });
  });

  test("a row with no entity: by-ref first, then the page, with no space", async ({ page }) => {
    test.setTimeout(120_000);
    const mock = await setupDirectories(page);
    await page.goto("/knowledge/directories/customer");
    await writeButton(page, "Penrose Works").click({ timeout: 60_000 });

    const dialog = page.getByRole("dialog", { name: "Write the page" });
    await expect(dialog).toContainText(
      "Everyone who can read this space will see this page and the name of this customer — including people who can't open it in CRM.",
    );
    // The space choice: Company and Operations, and never the `me` space.
    await dialog.getByRole("button", { name: /^Space/ }).click();
    await expect(page.getByRole("option", { name: "Operations" })).toBeVisible();
    await expect(page.getByRole("option", { name: "My notes" })).toHaveCount(0);
    await page.getByRole("option", { name: "Company" }).click();

    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-new-1\?edit=1$/, { timeout: 30_000 });
    const writes = mock.calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => c.path)).toEqual(["entities/by-ref", "entities/e-c-1/page"]);
    expect(writes[0].body).toEqual({ type: "customer", ref_id: "c-1" });
    expect(writes[1].body).toEqual({});
  });

  test("another space is sent as space_id", async ({ page }) => {
    test.setTimeout(120_000);
    const mock = await setupDirectories(page);
    await page.goto("/knowledge/directories/customer");
    await writeButton(page, "Acme Tools").click({ timeout: 60_000 });
    const dialog = page.getByRole("dialog", { name: "Write the page" });
    await dialog.getByRole("button", { name: /^Space/ }).click();
    await page.getByRole("option", { name: "Operations" }).click();
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-new-1\?edit=1$/, { timeout: 30_000 });
    const second = mock.calls.filter((c) => c.method === "POST")[1];
    expect(second.body).toEqual({ space_id: "ks-ops" });
  });

  test("a by-ref 404 shows its message and makes no page", async ({ page }) => {
    test.setTimeout(120_000);
    const mock = await setupDirectories(page, { byRef: 404 });
    await page.goto("/knowledge/directories/customer");
    await writeButton(page, "Penrose Works").click({ timeout: 60_000 });
    const dialog = page.getByRole("dialog", { name: "Write the page" });
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("You can't see that record any more.");
    expect(mock.calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["entities/by-ref"]);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Customers" })).toBeVisible();
  });

  test("a page 409 says the entity has a page", async ({ page }) => {
    test.setTimeout(120_000);
    await setupDirectories(page, { pageAnswer: 409 });
    await page.goto("/knowledge/directories/customer");
    await writeButton(page, "Penrose Works").click({ timeout: 60_000 });
    const dialog = page.getByRole("dialog", { name: "Write the page" });
    await dialog.getByRole("button", { name: "Create" }).click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "This customer already has a page. If you can't open it, it's a draft or in a space you can't read.",
    );
  });

  test("with the types read refused, the group hides and Spaces still works", async ({ page }) => {
    test.setTimeout(120_000);
    await setupDirectories(page, { types: "404" });
    await page.goto("/knowledge");
    await expect(rail(page).getByRole("button", { name: "Spaces" })).toBeVisible({ timeout: 60_000 });
    await expect(rail(page).getByText("Directories")).toHaveCount(0);
    await expect(rail(page).locator("[data-directory-type]")).toHaveCount(0);
    await rail(page).getByRole("button", { name: "Spaces" }).click();
    await expect(page.getByRole("tab", { name: "Recently updated" })).toBeVisible();
  });

  test("a row with a page links to it and has no Write button", async ({ page }) => {
    test.setTimeout(120_000);
    await setupDirectories(page);
    await page.goto("/knowledge/directories/customer");
    const row = page.locator('[data-entity-ref="c-2"]');
    await expect(row).toBeVisible({ timeout: 60_000 });
    await expect(row.getByRole("button", { name: /^Write the page/ })).toHaveCount(0);
    await expect(row.getByRole("link", { name: "Pendle Labs" })).toHaveAttribute("href", "/knowledge/pages/kp-9");
    await expect(row.getByRole("link", { name: "Page" })).toHaveAttribute("href", "/knowledge/pages/kp-9");
    await expect(row.getByRole("link", { name: "Open in CRM" })).toHaveAttribute("href", "/crm?organization=c-2");
  });

  test("an unknown directory says so", async ({ page }) => {
    await setupDirectories(page);
    await page.goto("/knowledge/directories/nothing");
    await expect(page.getByText("That directory does not exist")).toBeVisible({ timeout: 60_000 });
  });
});
