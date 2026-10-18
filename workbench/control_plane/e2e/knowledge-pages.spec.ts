import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * Knowledge spaces and pages (WS-61 KB-2a, `knowledge_base.md` §17).
 *
 * The BFF at `/api/knowledge/**` is a mock that keeps state, so the flow runs
 * end to end in the browser with no gateway: a member makes a page, edits it,
 * publishes it, and reverts it to version 1. The versions list then shows
 * create, edit, publish and revert, and the writes reach the BFF in that
 * order. The other tests hold the first-open phase (404 is off, 403 is no
 * feature, a page 404 is "not found" and never off) and the read-only member.
 *
 * The tree (KB-2e): a drop keeps its place after a reload, a page cannot
 * drop into its own subtree, a reader sees no drag, no `+` and no `…`, the
 * `+` and the `…` act on the row, and the arrow keys walk the rows.
 *
 * The real gateway half is `tests/unit/test_kb_pages_r8.py`.
 */

const SPACE_ID = "ks-company";
const ME_ID = "ks-me";

function member(features: string[] = ["knowledge"]) {
  return {
    authenticated: true,
    email: "asha@example.com",
    name: "Asha Rao",
    is_admin: false,
    features,
    permissions: [],
    capabilities: [],
    roles: ["member"],
    organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
  };
}

type Version = { version: number; title: string; body_md: string; reason: string };
type KbPage = {
  id: string;
  space_id: string;
  parent_id: string | null;
  title: string;
  body_md: string;
  state: "draft" | "published" | "archived";
  versions: Version[];
  /** The place among the siblings (KB-2e). Absent is 0. */
  position?: number;
};

interface MockOpts {
  /** The member's level on the Company space. */
  level: "read" | "write" | "manage";
  /** The status of `GET /knowledge/spaces`, when it refuses. */
  spacesStatus?: number;
  /** Pages that exist before the test starts. */
  pages?: KbPage[];
}

/** A stateful mock of the Knowledge BFF. Returns the log of each write. */
async function mockKnowledge(page: Page, opts: MockOpts) {
  const writes: Array<{ method: string; path: string; body: unknown }> = [];
  const pages = new Map<string, KbPage>((opts.pages ?? []).map((p) => [p.id, p]));
  let meExists = false;
  const now = new Date().toISOString();

  const summary = (p: KbPage) => ({
    id: p.id,
    space_id: p.space_id,
    parent_id: p.parent_id,
    position: p.position ?? 0,
    title: p.title,
    state: p.state,
    author_email: "asha@example.com",
    reviewers: [],
    review_at: p.state === "published" ? "2027-04-14T00:00:00Z" : null,
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: now,
    created_at: now,
  });
  const writable = (spaceId: string) => spaceId === ME_ID || opts.level !== "read";
  const detail = (p: KbPage) => ({
    ...summary(p),
    body_md: p.body_md,
    origin: null,
    level: p.space_id === ME_ID ? "manage" : opts.level,
    can_write: writable(p.space_id),
    versions: [...p.versions].reverse().map((v) => ({
      version: v.version,
      title: v.title,
      author_email: "asha@example.com",
      reason: v.reason,
      via: "web",
      created_at: now,
    })),
    links: [],
  });
  const written = (p: KbPage) => ({ ...summary(p), body_md: p.body_md, origin: null, version: p.versions.length });
  const version = (p: KbPage, reason: string) =>
    p.versions.push({ version: p.versions.length + 1, title: p.title, body_md: p.body_md, reason });
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    const method = req.method();
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    if (method !== "GET") writes.push({ method, path, body });

    if (path === "spaces") {
      if (opts.spacesStatus) return route.fulfill(json(opts.spacesStatus, { detail: "Not Found" }));
      const count = (id: string) => [...pages.values()].filter((p) => p.space_id === id).length;
      const spaces: Array<Record<string, unknown>> = [
        { id: SPACE_ID, slug: "company", name: "Company", purpose: "Products, services, policies, and how we do things.",
          icon: "", kind: "company", owner_email: null, archived_at: null, created_at: now, level: opts.level,
          page_count: count(SPACE_ID) },
      ];
      if (meExists) {
        spaces.push({ id: ME_ID, slug: "me", name: "My notes", purpose: "Your private drafts.", icon: "",
          kind: "me", owner_email: "asha@example.com", archived_at: null, created_at: now, level: "manage",
          page_count: count(ME_ID) });
      }
      return route.fulfill(json(200, { spaces }));
    }
    if (path === "pages" && method === "GET") {
      const space = url.searchParams.get("space");
      return route.fulfill(json(200, {
        pages: [...pages.values()].filter((p) => p.space_id === space && p.state !== "archived").map(summary),
      }));
    }
    if (path === "pages" && method === "POST") {
      const spaceId = body.space_id ?? ME_ID;
      if (!body.space_id) meExists = true;
      if (!writable(spaceId)) return route.fulfill(json(403, { detail: "Writing in this space needs a write grant." }));
      const made: KbPage = { id: `kp${pages.size + 1}`, space_id: spaceId, parent_id: body.parent_id ?? null,
        title: body.title, body_md: body.body_md ?? "", state: "draft", versions: [] };
      version(made, "create");
      pages.set(made.id, made);
      return route.fulfill(json(201, written(made)));
    }
    const m = /^pages\/([^/]+)(?:\/(publish|revert|move|archive))?$/.exec(path);
    const found = m ? pages.get(decodeURIComponent(m[1])) : undefined;
    if (!m || !found) return route.fulfill(json(404, { detail: "Not found" }));
    if (method === "GET") return route.fulfill(json(200, detail(found)));
    if (!writable(found.space_id)) return route.fulfill(json(403, { detail: "Needs a write grant." }));
    if (method === "PATCH") {
      if (typeof body.title === "string") found.title = body.title;
      if (typeof body.body_md === "string") found.body_md = body.body_md;
      version(found, "edit");
      return route.fulfill(json(200, written(found)));
    }
    if (m[2] === "publish") {
      found.state = "published";
      version(found, "publish");
      return route.fulfill(json(200, written(found)));
    }
    if (m[2] === "move") {
      // The gateway's rule (`pages._place`): the page goes to its place among
      // the new siblings, and they get 0 to n. Only a new parent writes a version.
      const parent = body && "parent_id" in body ? body.parent_id : found.parent_id;
      const siblings = [...pages.values()]
        .filter((p) => p.space_id === found.space_id && p.parent_id === parent && p.id !== found.id)
        .sort((a, b) => (a.position ?? 0) - (b.position ?? 0) || a.title.localeCompare(b.title));
      siblings.splice(Math.min(body?.position ?? siblings.length, siblings.length), 0, found);
      const moved = parent !== found.parent_id;
      found.parent_id = parent;
      siblings.forEach((p, i) => (p.position = i));
      if (moved) version(found, "move");
      return route.fulfill(json(200, summary(found)));
    }
    if (m[2] === "archive") {
      found.state = "archived";
      version(found, "archive");
      return route.fulfill(json(200, written(found)));
    }
    if (m[2] === "revert") {
      const old = found.versions.find((v) => v.version === body.version);
      if (!old) return route.fulfill(json(404, { detail: "Not found" }));
      found.title = old.title;
      found.body_md = old.body_md;
      version(found, "revert");
      return route.fulfill(json(200, written(found)));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  /** Another member saves a new body meanwhile: one more version on the server. */
  const bump = (id: string, body: string) => {
    const p = pages.get(id) as KbPage;
    p.body_md = body;
    version(p, "edit");
  };
  return { writes, pages, bump };
}

/** Two published pages in Company, so the rail has a row to click away to. */
function twoPages(): KbPage[] {
  const v = (title: string, body: string): Version[] => [
    { version: 1, title, body_md: "", reason: "create" },
    { version: 2, title, body_md: body, reason: "edit" },
  ];
  return [
    { id: "kp-a", space_id: SPACE_ID, parent_id: null, title: "Refund policy", body_md: "Thirty days.",
      state: "published", versions: v("Refund policy", "Thirty days.") },
    { id: "kp-b", space_id: SPACE_ID, parent_id: null, title: "Holidays", body_md: "Ten days.",
      state: "published", versions: v("Holidays", "Ten days.") },
  ];
}

/** Open page A in the editor and type into its body. */
async function startTyping(page: Page) {
  await page.goto("/knowledge/pages/kp-a");
  await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
  const body = page.getByRole("textbox", { name: "Page body in Markdown" });
  await body.fill("Typed text");
  return body;
}

async function setup(page: Page, features?: string[]) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member(features) });
}

test.describe("Knowledge spaces and pages", () => {
  test("a member makes a page, edits it, publishes it and reverts it to version 1", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write" });

    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Spaces" })).toBeVisible();

    // Make a page in the Company space.
    await page.getByRole("link", { name: /Company/ }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Company" })).toBeVisible();
    await page.getByRole("button", { name: "New page" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill("Refund policy");
    await dialog.getByRole("button", { name: "Create page" }).click();

    // A new page opens in the editor.
    const body = page.getByRole("textbox", { name: "Page body in Markdown" });
    await expect(body).toBeVisible({ timeout: 15_000 });
    await body.fill("# Refunds\n\nWe refund within **30 days**.");
    await page.getByRole("tab", { name: "Preview" }).click();
    await expect(page.locator(".cc-prose strong", { hasText: "30 days" })).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    // The body's `# Refunds` draws one level down, so the app bar keeps the one h1.
    await expect(page.getByRole("heading", { name: "Refunds", exact: true })).toBeVisible();
    const h1 = await page.evaluate(() =>
      Array.from(document.querySelectorAll("h1")).filter((h) => h.getClientRects().length > 0).map((h) => h.textContent),
    );
    expect(h1).toEqual(["Knowledge"]);

    // Publish through the confirmation.
    await page.getByRole("button", { name: "Publish", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Publish page" }).click();
    await expect(page.locator("main").getByText("Published", { exact: true }).first()).toBeVisible();

    // Revert to version 1 through the confirmation.
    await page.getByRole("button", { name: "Revert to version 1" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Revert to version 1" }).click();

    const versions = page.getByRole("list", { name: "Versions" });
    await expect(versions.locator("li")).toHaveCount(4);
    await expect(versions.locator("li").nth(0)).toContainText("Reverted");
    await expect(versions.locator("li").nth(1)).toContainText("Published");
    await expect(versions.locator("li").nth(2)).toContainText("Edited");
    await expect(versions.locator("li").nth(3)).toContainText("Created");
    // Version 1 had no body, so the page is empty again.
    await expect(page.getByText("This page is empty.")).toBeVisible();

    // The writes reached the BFF in this order, with these bodies.
    expect(kb.writes.map((w) => `${w.method} ${w.path.replace(/kp\d+/, ":id")}`)).toEqual([
      "POST pages",
      "PATCH pages/:id",
      "POST pages/:id/publish",
      "POST pages/:id/revert",
    ]);
    expect(kb.writes[0].body).toEqual({ space_id: SPACE_ID, title: "Refund policy" });
    expect(kb.writes[1].body).toEqual({ body_md: "# Refunds\n\nWe refund within **30 days**." });
    expect(kb.writes[3].body).toEqual({ version: 1 });

    // The rail shows the page in the space's tree.
    await expect(page.getByRole("navigation", { name: "Knowledge spaces" }).getByText("Refund policy")).toBeVisible();
  });

  test("a sub-page names its parent", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, {
      level: "write",
      pages: [{ id: "kp-root", space_id: SPACE_ID, parent_id: null, title: "Handbook", body_md: "Hi",
        state: "published", versions: [{ version: 1, title: "Handbook", body_md: "Hi", reason: "create" }] }],
    });
    await page.goto("/knowledge/pages/kp-root");
    // Exact: the rail's row now has its own "New sub-page under Handbook" (KB-2e).
    await page.getByRole("button", { name: "New sub-page", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Under Handbook in Company");
    await dialog.getByRole("textbox").fill("Leave");
    await dialog.getByRole("button", { name: "Create page" }).click();
    await expect(page.getByRole("textbox", { name: "Page body in Markdown" })).toBeVisible();
    expect(kb.writes[0].body).toEqual({ space_id: SPACE_ID, parent_id: "kp-root", title: "Leave" });
  });

  test("a page made from a read-only space lands in My notes", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "read" });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(page.getByRole("heading", { level: 2, name: "Company" })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "New page" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: /Space/ })).toContainText("My notes");
    await dialog.getByRole("textbox").fill("Ideas");
    await dialog.getByRole("button", { name: "Create page" }).click();
    await expect(page.getByRole("textbox", { name: "Page body in Markdown" })).toBeVisible();
    // No space in the body: the gateway puts the page in `me`.
    expect(kb.writes[0].body).toEqual({ title: "Ideas" });
  });

  test("a reader sees no write control", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, {
      level: "read",
      pages: [{ id: "kp-pub", space_id: SPACE_ID, parent_id: null, title: "Holidays", body_md: "Ten days.",
        state: "published", versions: [
          { version: 1, title: "Holidays", body_md: "", reason: "create" },
          { version: 2, title: "Holidays", body_md: "Ten days.", reason: "edit" },
          { version: 3, title: "Holidays", body_md: "Ten days.", reason: "publish" },
        ] }],
    });
    await page.goto("/knowledge/pages/kp-pub");
    await expect(page.getByRole("heading", { level: 2, name: "Holidays" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Read only")).toBeVisible();
    await expect(page.getByRole("list", { name: "Versions" }).locator("li")).toHaveCount(3);
    for (const name of ["Edit", "Publish", "New sub-page", "Revert to version 1", "Revert to version 2"]) {
      await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    }
  });

  test("a page 404 is not found, and never the off state", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write" });
    await page.goto("/knowledge/pages/missing");
    await expect(page.getByText("This page is not here.")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Knowledge is not turned on")).toHaveCount(0);
  });

  test("with the flag off, /knowledge shows the off state under its one h1", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", spacesStatus: 404 });
    await page.goto("/knowledge");
    await expect(page.getByText("Knowledge is not turned on for your organization yet.")).toBeVisible({ timeout: 30_000 });
    const h1 = await page.evaluate(
      () => Array.from(document.querySelectorAll("h1")).filter((h) => h.getClientRects().length > 0).map((h) => h.textContent),
    );
    expect(h1).toEqual(["Knowledge"]);
    await expect(page.getByRole("button", { name: "New page" })).toHaveCount(0);
  });

  test("a 403 on the spaces read says the member has no access", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", spacesStatus: 403 });
    await page.goto("/knowledge");
    await expect(page.getByText("You do not have access to Knowledge")).toBeVisible({ timeout: 30_000 });
  });
});

test.describe("an edit is never lost in silence", () => {
  test("a rail row asks first, and Keep editing keeps the text", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: twoPages() });
    const body = await startTyping(page);
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });
    await rail.getByRole("button", { name: "Holidays", exact: true }).click();
    const ask = page.getByRole("dialog", { name: "Discard your changes?" });
    await expect(ask).toBeVisible();
    await ask.locator("[data-cancel]").filter({ hasText: "Keep editing" }).click();
    await expect(body).toHaveValue("Typed text");
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-a$/);

    // Discard leaves, and writes nothing.
    await rail.getByRole("button", { name: "Holidays", exact: true }).click();
    await page.getByRole("dialog", { name: "Discard your changes?" }).getByRole("button", { name: "Discard" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Holidays" })).toBeVisible();
    expect(kb.writes).toEqual([]);
  });

  test("the space link, New page and Cancel ask too", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: twoPages() });
    const body = await startTyping(page);
    const ask = page.getByRole("dialog", { name: "Discard your changes?" });
    for (const open of [
      () => page.locator("article").getByRole("link", { name: "Company" }).click(),
      () => page.getByRole("button", { name: "New page" }).first().click(),
      () => page.getByRole("button", { name: "Cancel", exact: true }).click(),
    ]) {
      await open();
      await expect(ask).toBeVisible();
      await ask.locator("[data-cancel]").filter({ hasText: "Keep editing" }).click();
      await expect(ask).toHaveCount(0);
      await expect(body).toHaveValue("Typed text");
    }
  });

  test("closing the tab warns while the draft is dirty", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: twoPages() });
    await startTyping(page);
    const dialog = page.waitForEvent("dialog");
    await page.close({ runBeforeUnload: true });
    const d = await dialog;
    expect(d.type()).toBe("beforeunload");
    await d.dismiss();
  });

  test("the draft comes back after the switch to the phone frame", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: twoPages() });
    await startTyping(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("textbox", { name: "Page body in Markdown" })).toHaveValue("Typed text", { timeout: 15_000 });
  });

  test("no Revert while the editor is open", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: twoPages() });
    await page.goto("/knowledge/pages/kp-a");
    await expect(page.getByRole("button", { name: "Revert to version 1" })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByRole("button", { name: "Revert to version 1" })).toHaveCount(0);
  });
});

test.describe("a save never overwrites a newer version in silence", () => {
  test("it stops, and Save anyway sends only what the member changed", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: twoPages() });
    await startTyping(page);
    kb.bump("kp-a", "Their body");
    await page.getByRole("button", { name: "Save changes" }).click();
    const notice = page.getByRole("alert").filter({ hasText: "Someone saved a newer version" });
    await expect(notice).toBeVisible();
    expect(kb.writes).toEqual([]);
    await notice.getByRole("button", { name: "Save anyway" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["PATCH pages/kp-a"]);
    expect(kb.writes[0].body).toEqual({ body_md: "Typed text" });
  });

  test("Reload drops the draft and shows the newer body", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: twoPages() });
    await startTyping(page);
    kb.bump("kp-a", "Their body");
    await page.getByRole("button", { name: "Save changes" }).click();
    const notice = page.getByRole("alert").filter({ hasText: "Someone saved a newer version" });
    await notice.getByRole("button", { name: "Reload" }).click();
    await expect(page.locator(".cc-prose")).toContainText("Their body");
    await expect(page.getByRole("textbox", { name: "Page body in Markdown" })).toHaveCount(0);
    expect(kb.writes).toEqual([]);
  });
});

// ── The tree (WS-61 KB-2e) ─────────────────────────────────────────────────

function treePage(id: string, title: string, parent: string | null, position: number): KbPage {
  return { id, space_id: SPACE_ID, parent_id: parent, title, body_md: "Text.", state: "published", position,
    versions: [{ version: 1, title, body_md: "Text.", reason: "create" }] };
}

/** Handbook (with Leave under it), Refund policy, Travel, at the root of Company. */
function treePages(): KbPage[] {
  return [
    treePage("kp-h", "Handbook", null, 0),
    treePage("kp-l", "Leave", "kp-h", 0),
    treePage("kp-r", "Refund policy", null, 1),
    treePage("kp-t", "Travel", null, 2),
  ];
}

/** The page ids of the rail's rows, top to bottom. */
async function railOrder(page: Page): Promise<string[]> {
  return page
    .getByRole("navigation", { name: "Knowledge spaces" })
    .locator("[data-page-id]")
    .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-page-id") ?? ""));
}

function row(page: Page, id: string) {
  return page.getByRole("navigation", { name: "Knowledge spaces" }).locator(`[data-page-id="${id}"]`);
}

test.describe("the tree (KB-2e)", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test("a drop between two pages keeps its place after a reload", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });
    expect(await railOrder(page)).toEqual(["kp-h", "kp-r", "kp-t"]);

    // Travel onto the top edge of Refund policy: between Handbook and Refund policy.
    await row(page, "kp-t").dragTo(row(page, "kp-r"), { targetPosition: { x: 40, y: 2 } });
    await expect.poll(() => kb.writes.length).toBe(1);
    expect(kb.writes[0]).toEqual({ method: "POST", path: "pages/kp-t/move", body: { position: 1 } });
    await expect.poll(() => railOrder(page)).toEqual(["kp-h", "kp-t", "kp-r"]);

    await page.reload();
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });
    expect(await railOrder(page)).toEqual(["kp-h", "kp-t", "kp-r"]);
    // A reorder writes no version.
    expect(kb.pages.get("kp-t")?.versions.map((v) => v.reason)).toEqual(["create"]);
  });

  test("a drop on the middle of a row nests the page under it", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });
    await row(page, "kp-t").dragTo(row(page, "kp-r"));
    await expect.poll(() => kb.writes.length).toBe(1);
    expect(kb.writes[0].body).toEqual({ parent_id: "kp-r", position: 0 });
    // The new parent opens, so the page stays in sight.
    await expect.poll(() => railOrder(page)).toEqual(["kp-h", "kp-r", "kp-t"]);
    await expect(page.getByRole("button", { name: "Collapse Refund policy" })).toBeVisible();
  });

  test("the drop line starts at the depth of the place it means", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto("/knowledge/pages/kp-l");
    await expect(row(page, "kp-l")).toBeVisible({ timeout: 30_000 });
    const from = await row(page, "kp-t").boundingBox();
    const leave = await row(page, "kp-l").boundingBox();
    const leaveIcon = await row(page, "kp-l").locator("[data-rail-icon]").boundingBox();
    const refundIcon = await row(page, "kp-r").locator("[data-rail-icon]").boundingBox();
    const refund = await row(page, "kp-r").boundingBox();
    if (!from || !leave || !leaveIcon || !refund || !refundIcon) throw new Error("no row box");
    const line = page.getByRole("navigation", { name: "Knowledge spaces" }).locator("[data-drop-line]");

    await page.mouse.move(from.x + 40, from.y + from.height / 2);
    await page.mouse.down();
    // The top edge of Leave: a place under Handbook, one level in.
    await page.mouse.move(leave.x + 60, leave.y + leave.height / 2, { steps: 6 });
    await page.mouse.move(leave.x + 60, leave.y + 3, { steps: 3 });
    await expect(line).toHaveCount(1);
    const nested = await line.boundingBox();
    expect(Math.abs((nested?.x ?? 0) - leaveIcon.x)).toBeLessThan(3);
    // The top edge of Refund policy, after the open branch: a place at the root.
    await page.mouse.move(refund.x + 60, refund.y + refund.height / 2, { steps: 3 });
    await page.mouse.move(refund.x + 60, refund.y + 3, { steps: 3 });
    await expect(line).toHaveCount(1);
    const root = await line.boundingBox();
    expect(Math.abs((root?.x ?? 0) - refundIcon.x)).toBeLessThan(3);
    expect(leaveIcon.x - refundIcon.x).toBeGreaterThan(10);
    // Let go away from the tree: nothing moves.
    await page.mouse.move(1000, 700, { steps: 4 });
    await page.mouse.up();
    expect(kb.writes).toEqual([]);
  });

  test("a page cannot drop into its own subtree", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto("/knowledge/pages/kp-l");
    await expect(row(page, "kp-l")).toBeVisible({ timeout: 30_000 });
    await row(page, "kp-h").dragTo(row(page, "kp-l"));
    await row(page, "kp-h").dragTo(row(page, "kp-h"));
    // Give a refused drop the time a real one takes to reach the BFF.
    await page.waitForTimeout(500);
    expect(kb.writes).toEqual([]);
    expect(await railOrder(page)).toEqual(["kp-h", "kp-l", "kp-r", "kp-t"]);
  });

  test("a reader sees no handle, no + and no …", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "read", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });
    await row(page, "kp-r").hover();
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });
    await expect(rail.locator('[data-page-id][draggable="true"]')).toHaveCount(0);
    await expect(rail.getByRole("button", { name: /^Actions for / })).toHaveCount(0);
    await expect(rail.getByRole("button", { name: /^New sub-page under / })).toHaveCount(0);
  });

  test("the + opens the New page form with the row as the parent", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-r")).toBeVisible({ timeout: 30_000 });
    await row(page, "kp-r").hover();
    await page.getByRole("button", { name: "New sub-page under Refund policy" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Under Refund policy in Company");
    await dialog.getByRole("textbox").fill("Exceptions");
    await dialog.getByRole("button", { name: "Create page" }).click();
    await expect(page.getByRole("textbox", { name: "Page body in Markdown" })).toBeVisible({ timeout: 15_000 });
    expect(kb.writes[0].body).toEqual({ space_id: SPACE_ID, parent_id: "kp-r", title: "Exceptions" });
  });

  test("the … holds Copy link, New sub-page and Archive", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await setup(page);
    const kb = await mockKnowledge(page, { level: "manage", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });

    const openMenu = async () => {
      await row(page, "kp-t").hover();
      await page.getByRole("button", { name: "Actions for Travel" }).click();
      return page.getByRole("menu");
    };
    let menu = await openMenu();
    await expect(menu.getByRole("menuitem")).toHaveText(["Copy link", "New sub-page", "Archive"]);
    await menu.getByRole("menuitem", { name: "Copy link" }).click();
    await expect(page.getByText("Link copied")).toBeVisible();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toMatch(/\/knowledge\/pages\/kp-t$/);

    menu = await openMenu();
    await menu.getByRole("menuitem", { name: "Archive" }).click();
    const ask = page.getByRole("dialog", { name: "Archive this page?" });
    await expect(ask).toContainText("Travel");
    await ask.getByRole("button", { name: "Archive page" }).click();
    await expect(row(page, "kp-t")).toHaveCount(0);
    expect(kb.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["POST pages/kp-t/archive"]);
  });

  test("a writer without manage gets no Archive", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-t")).toBeVisible({ timeout: 30_000 });
    await row(page, "kp-t").hover();
    await page.getByRole("button", { name: "Actions for Travel" }).click();
    await expect(page.getByRole("menu").getByRole("menuitem")).toHaveText(["Copy link", "New sub-page"]);
  });

  test("the arrow keys walk the rows, and open and close a row", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "read", pages: treePages() });
    await page.goto(`/knowledge?space=${SPACE_ID}`);
    await expect(row(page, "kp-h")).toBeVisible({ timeout: 30_000 });
    const label = (id: string) => row(page, id).locator("[data-rail-label]");

    await label("kp-h").focus();
    await page.keyboard.press("ArrowDown");
    await expect(label("kp-r")).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(label("kp-t")).toBeFocused();
    // The end holds.
    await page.keyboard.press("ArrowDown");
    await expect(label("kp-t")).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    await expect(label("kp-h")).toBeFocused();

    // ArrowRight opens Handbook, and ArrowDown then reaches Leave under it.
    await page.keyboard.press("ArrowRight");
    await expect(row(page, "kp-l")).toBeVisible();
    await page.keyboard.press("ArrowDown");
    await expect(label("kp-l")).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowLeft");
    await expect(row(page, "kp-l")).toHaveCount(0);
    // Up from the first page reaches the space row above it.
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("navigation", { name: "Knowledge spaces" }).getByRole("button", { name: /^Company/ }).first()).toBeFocused();
  });
});
