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
  const body = page.getByRole("textbox", { name: "Page body" });
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
    const body = page.getByRole("textbox", { name: "Page body" });
    await expect(body).toBeVisible({ timeout: 15_000 });
    // The page is the editor: Markdown shortcuts work as you type (KB-2b).
    await body.click();
    await page.keyboard.type("# Refunds");
    await page.keyboard.press("Enter");
    await page.keyboard.type("We refund within **30 days**.");
    await expect(body.locator("strong", { hasText: "30 days" })).toBeVisible();
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
    await expect(page.getByRole("textbox", { name: "Page body" })).toBeVisible();
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
    await expect(page.getByRole("textbox", { name: "Page body" })).toBeVisible();
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
    await expect(body).toHaveText("Typed text");
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
      await expect(body).toHaveText("Typed text");
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
    await expect(page.getByRole("textbox", { name: "Page body" })).toHaveText("Typed text", { timeout: 15_000 });
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
    await expect(page.getByRole("textbox", { name: "Page body" })).toHaveCount(0);
    expect(kb.writes).toEqual([]);
  });
});

// ── The block editor (WS-61 KB-2b, `knowledge_base.md` §21.5) ───────────────

/** One page with this body, and the Holidays page to link to. */
function pageWith(body: string): KbPage[] {
  const [, holidays] = twoPages();
  return [
    { id: "kp-a", space_id: SPACE_ID, parent_id: null, title: "Refund policy", body_md: body, state: "published",
      versions: [{ version: 1, title: "Refund policy", body_md: body, reason: "create" }] },
    holidays,
  ];
}

/**
 * Put the caret at the end of the body through the editor. A native
 * Ctrl+End moves the browser's selection, and ProseMirror reads it on the
 * next `selectionchange`, so a key pressed at once can land at the old place.
 */
async function caretAtEnd(page: Page) {
  await page.locator(".ProseMirror[contenteditable='true']").evaluate((el) => {
    (el as unknown as { editor: { commands: { focus: (at: string) => boolean } } }).editor.commands.focus("end");
  });
  // TipTap focuses on the next frame. A key typed before that is lost.
  await expect(page.getByRole("textbox", { name: "Page body" })).toBeFocused();
}

/** Open page A in the editor, and put the caret at the end of its body. */
async function openEditor(page: Page) {
  await page.goto("/knowledge/pages/kp-a");
  await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
  const body = page.getByRole("textbox", { name: "Page body" });
  await expect(body).toBeVisible();
  await caretAtEnd(page);
  return body;
}

/** Start a new empty paragraph at the end of the body. */
async function newLine(page: Page) {
  await caretAtEnd(page);
  await page.keyboard.press("Enter");
}

test.describe("the block editor", () => {
  test("the / menu inserts each block of the set", async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    await page.goto("/knowledge/pages/kp-a");
    const menu = page.getByRole("listbox", { name: "Blocks" });
    const body = page.getByRole("textbox", { name: "Page body" });

    // Each block in an empty body, with one word typed into it, then saved.
    const blocks: Array<[string, string, string]> = [
      ["Heading 1", "h2[data-md-level='1']", "# Item"],
      ["Heading 2", "h3[data-md-level='2']", "## Item"],
      ["Bulleted list", "ul:not([data-type])", "- Item"],
      ["Numbered list", "ol", "1. Item"],
      ["Task list", "ul[data-type='taskList']", "- [ ] Item"],
      ["Quote", "blockquote", "> Item"],
      ["Note callout", ".kb-callout[data-callout='note']", "> [!NOTE]\n> Item"],
      ["Tip callout", ".kb-callout[data-callout='tip']", "> [!TIP]\n> Item"],
      ["Important callout", ".kb-callout[data-callout='important']", "> [!IMPORTANT]\n> Item"],
      ["Warning callout", ".kb-callout[data-callout='warning']", "> [!WARNING]\n> Item"],
      ["Caution callout", ".kb-callout[data-callout='caution']", "> [!CAUTION]\n> Item"],
      ["Toggle", ".kb-toggle", "<details>\n<summary>Item</summary>\n\n</details>"],
      ["Code block", ".kb-code", "```\nItem\n```"],
      ["Table", "table", "| Item |  |  |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |"],
      ["Divider", "hr", "---\n\nItem"],
      ["Text", "p", "Item"],
    ];
    for (const [label, selector, saved] of blocks) {
      await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
      await body.click();
      await page.keyboard.press("ControlOrMeta+a");
      await page.keyboard.press("Backspace");
      await page.keyboard.type(`/${label}`);
      await expect(menu, label).toBeVisible();
      await expect(menu.getByRole("option").first(), label).toHaveText(label);
      await page.keyboard.press("Enter");
      await expect(menu).toHaveCount(0);
      await expect(body.locator(selector).first(), label).toBeVisible();
      await page.keyboard.type("Item");
      const before = kb.writes.length;
      await page.getByRole("button", { name: "Save changes" }).click();
      await expect.poll(() => kb.writes.length).toBe(before + 1);
      // Wait for the re-read, so the next edit starts from the new version.
      await expect(page.getByRole("list", { name: "Versions" }).locator("li")).toHaveCount(before + 2);
      const last = kb.writes[kb.writes.length - 1].body as { body_md: string };
      expect(last.body_md, label).toBe(saved);
    }

    // An image asks for its address, and a remote one draws behind the gate.
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await body.click();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.press("Backspace");
    await page.keyboard.type("/Image");
    await expect(menu.getByRole("option").first()).toHaveText("Image");
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Add an image" });
    await dialog.getByRole("textbox", { name: "Image address" }).fill("https://example.com/chart.png");
    await dialog.getByRole("textbox", { name: "What the image shows" }).fill("A chart");
    await dialog.getByRole("button", { name: "Add image" }).click();
    await expect(body.getByText("Image from example.com")).toBeVisible();
    await expect(body.locator("img[src='https://example.com/chart.png']")).toHaveCount(0);
    const before = kb.writes.length;
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(before + 1);
    await expect(page.getByRole("list", { name: "Versions" }).locator("li")).toHaveCount(before + 2);
    expect(kb.writes[kb.writes.length - 1].body).toEqual({ body_md: "![A chart](https://example.com/chart.png)" });

    // "Link to a page" opens the page picker.
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await caretAtEnd(page);
    await page.keyboard.press("Enter");
    await page.keyboard.type("/Link to a page");
    await expect(menu.getByRole("option").first()).toHaveText("Link to a page");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("listbox", { name: "Pages" })).toBeVisible();
  });

  test("the / menu inserts each KB-2p block, and a save writes its stored form", async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    await page.goto("/knowledge/pages/kp-a");
    const menu = page.getByRole("listbox", { name: "Blocks" });
    const body = page.getByRole("textbox", { name: "Page body" });
    const today = await page.evaluate(() => {
      const d = new Date();
      const pad = (n: number) => String(n).padStart(2, "0");
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      return { iso: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, label: `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}` };
    });

    // Each block in an empty body. `act` finishes it, and `saved` is the body.
    const blocks: Array<{ label: string; selector: string; act?: () => Promise<void>; saved: string }> = [
      {
        label: "Status",
        selector: ".kb-status",
        act: async () => {
          const popover = page.getByRole("dialog", { name: "Status" });
          await expect(popover.getByRole("textbox", { name: "Status text" })).toBeFocused();
          await page.keyboard.type("Done");
          await popover.getByRole("button", { name: "Green" }).click();
          await popover.getByRole("textbox", { name: "Status text" }).press("Enter");
          await expect(popover).toHaveCount(0);
        },
        saved: '<span data-type="status" data-color="green">Done</span>',
      },
      {
        label: "Date",
        selector: "time.kb-date, .kb-date",
        act: async () => {
          const picker = page.getByRole("dialog", { name: "Date" });
          await expect(picker.getByLabel("Date")).toBeFocused();
          await page.keyboard.press("Escape");
          await expect(picker).toHaveCount(0);
        },
        saved: `<time data-type="date" datetime="${today.iso}">${today.label}</time>`,
      },
      {
        label: "2 columns",
        selector: ".kb-columns .kb-column",
        act: async () => {
          await page.keyboard.type("Item");
        },
        saved: '<div data-type="columns" data-count="2">\n\n<div data-type="column">\n\nItem\n\n</div>\n\n<div data-type="column">\n\n</div>\n\n</div>',
      },
      {
        label: "Sub-pages",
        selector: ".kb-subpages",
        saved: '<div data-type="subpages" data-order="manual"></div>',
      },
      {
        label: "Page break",
        selector: ".kb-page-break",
        saved: '<div data-type="page-break"></div>',
      },
    ];
    for (const { label, selector, act, saved } of blocks) {
      await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
      await body.click();
      await page.keyboard.press("ControlOrMeta+a");
      await page.keyboard.press("Backspace");
      await page.keyboard.type(`/${label}`);
      await expect(menu, label).toBeVisible();
      await expect(menu.getByRole("option").first(), label).toHaveText(label);
      await page.keyboard.press("Enter");
      await expect(menu).toHaveCount(0);
      await expect(body.locator(selector).first(), label).toBeVisible();
      if (act) await act();
      const before = kb.writes.length;
      await page.getByRole("button", { name: "Save changes" }).click();
      await expect.poll(() => kb.writes.length, label).toBe(before + 1);
      await expect(page.getByRole("list", { name: "Versions" }).locator("li")).toHaveCount(before + 2);
      const last = kb.writes[kb.writes.length - 1].body as { body_md: string };
      expect(last.body_md, label).toBe(saved);
    }

    // The count switcher of columns: 2 becomes 3, and a save writes 3.
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await body.click();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.press("Backspace");
    await page.keyboard.type("/2 columns");
    await expect(menu.getByRole("option").first()).toHaveText("2 columns");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Left");
    await body.getByRole("button", { name: "3 columns" }).click();
    await expect(body.locator(".kb-column")).toHaveCount(3);
    const before = kb.writes.length;
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(before + 1);
    const cols = (kb.writes[kb.writes.length - 1].body as { body_md: string }).body_md;
    expect(cols).toContain('<div data-type="columns" data-count="3">');
    expect(cols.match(/<div data-type="column">/g)).toHaveLength(3);
  });

  test("the bubble menu sets colour, highlight, sup, sub and alignment", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);
    const bubble = page.getByRole("toolbar", { name: "Format the selection" });

    const lines: Array<[string, (b: import("@playwright/test").Locator) => Promise<void>, string]> = [
      ["Blue words", async (b) => {
        await b.getByRole("button", { name: "Colour", exact: true }).click();
        await b.getByRole("button", { name: "Blue text" }).click();
      }, '<span data-color="blue">Blue words</span>'],
      ["Marked words", async (b) => {
        await b.getByRole("button", { name: "Colour", exact: true }).click();
        await b.getByRole("button", { name: "Yellow highlight" }).click();
      }, '<mark data-color="yellow">Marked words</mark>'],
      ["Up words", async (b) => b.getByRole("button", { name: "Superscript" }).click(), "<sup>Up words</sup>"],
      ["Down words", async (b) => b.getByRole("button", { name: "Subscript" }).click(), "<sub>Down words</sub>"],
      ["Centre words", async (b) => {
        await b.getByRole("button", { name: "Alignment", exact: true }).click();
        await b.getByRole("button", { name: "Align centre" }).click();
      }, '<p data-align="center">Centre words</p>'],
    ];
    // Type every line first: a colour carries on to the next line typed, as
    // in a word processor, so each line gets its mark after all are typed.
    for (const [text] of lines) {
      await newLine(page);
      await page.keyboard.type(text);
    }
    for (const [text, act] of lines) {
      await body.locator("p", { hasText: text }).selectText();
      await expect(bubble).toBeVisible();
      await act(bubble);
      await page.keyboard.press("ArrowRight");
      await bubble.waitFor({ state: "hidden" });
    }
    await expect(body.locator(".kb-text-color[data-color='blue']")).toHaveText("Blue words");
    await expect(body.locator("mark.kb-highlight[data-color='yellow']")).toHaveText("Marked words");
    await expect(body.locator("p[data-align='center']")).toHaveText("Centre words");

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    const saved = (kb.writes[0].body as { body_md: string }).body_md;
    for (const [, , form] of lines) expect(saved).toContain(form);
    // No raw style reaches the page: a colour name draws through its token.
    const styled = await page.locator("article .kb-editor [style]").count();
    expect(styled).toBe(0);
  });

  test("the sub-pages block lists the child pages, and the order changes the list", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const pages = pageWith('Intro.\n\n<div data-type="subpages" data-order="manual"></div>');
    const child = (id: string, title: string, position: number): KbPage => ({
      id, space_id: SPACE_ID, parent_id: "kp-a", title, body_md: "", state: "published", position,
      versions: [{ version: 1, title, body_md: "", reason: "create" }],
    });
    pages.push(child("kp-c", "Zebra rules", 0), child("kp-d", "Apple rules", 1));
    const kb = await mockKnowledge(page, { level: "write", pages });
    await page.goto("/knowledge/pages/kp-a");
    const list = page.locator("article .kb-editor").getByRole("list", { name: "Sub-pages" });
    await expect(list.getByRole("link")).toHaveText(["Zebra rules", "Apple rules"]);
    // A click opens the sub-page in place.
    await list.getByRole("link", { name: "Apple rules" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-d$/);

    await page.goto("/knowledge/pages/kp-a");
    await page.getByRole("button", { name: "Edit", exact: true }).click({ timeout: 30_000 });
    await page.getByRole("button", { name: "Order of the sub-pages" }).click();
    await page.getByRole("option", { name: "A to Z" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(1);
    expect((kb.writes[0].body as { body_md: string }).body_md).toBe(
      'Intro.\n\n<div data-type="subpages" data-order="title"></div>',
    );
    await expect(list.getByRole("link")).toHaveText(["Apple rules", "Zebra rules"]);
  });

  test("a merged cell, a header column and a cell colour save the table as HTML", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);
    await page.keyboard.press("Enter");
    await page.keyboard.type("/Table");
    await expect(page.getByRole("listbox", { name: "Blocks" }).getByRole("option").first()).toHaveText("Table");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Quarter");

    // A drag across the first two header cells selects them, and a bubble
    // offers Merge cells.
    const heads = body.locator("th");
    const a = await heads.nth(0).boundingBox();
    const b = await heads.nth(1).boundingBox();
    if (!a || !b) throw new Error("no header cells");
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
    await page.mouse.up();
    const cells = page.getByRole("toolbar", { name: "Selected cells" });
    await expect(cells).toBeVisible();
    await cells.getByRole("button", { name: "Merge cells" }).click();
    await expect(body.locator("th[colspan='2']")).toHaveCount(1);

    // The caret in a body cell: the table tools add a header column and a colour.
    await body.locator("td").first().click();
    const tools = page.getByRole("toolbar", { name: "Table" });
    await expect(tools).toBeVisible();
    await tools.getByRole("button", { name: "Turn the header column on or off" }).click();
    await body.locator("td").first().click();
    await tools.getByRole("button", { name: "Cell colour" }).click();
    await tools.getByRole("button", { name: "Blue cell" }).click();
    await expect(body.locator("td[data-color='blue']")).toHaveCount(1);

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(1);
    const saved = (kb.writes[0].body as { body_md: string }).body_md;
    expect(saved.startsWith("Start.\n\n<table>\n<tr>\n")).toBe(true);
    expect(saved).toContain('<th colspan="2">\n\nQuarter\n\n</th>');
    expect(saved).toContain('<td data-color="blue"></td>');
    expect(saved).toMatch(/<\/tr>\n<tr>\n<th><\/th>\n/);
    expect(saved.endsWith("</table>")).toBe(true);
    // The read view draws the same table from the stored HTML form.
    const read = page.locator("article .kb-editor");
    await expect(read.locator("th[colspan='2']")).toHaveText("Quarter");
    await expect(read.locator("td[data-color='blue']")).toHaveCount(1);
  });

  test("the bubble menu sets each mark, and the editor binds no Ctrl+K", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);
    const marks: Array<[string, string]> = [
      ["Bold", "strong"],
      ["Italic", "em"],
      ["Strikethrough", "s"],
      ["Inline code", "code"],
    ];
    for (const [label, tag] of marks) {
      await newLine(page);
      await page.keyboard.type(`${label} words`);
      await body.locator("p", { hasText: `${label} words` }).selectText();
      const bubble = page.getByRole("toolbar", { name: "Format the selection" });
      await expect(bubble).toBeVisible();
      await bubble.getByRole("button", { name: label, exact: true }).click();
      await expect(body.locator(tag, { hasText: `${label} words` })).toBeVisible();
      // Collapse the selection, and leave the mark, before the next line.
      await page.keyboard.press("ArrowRight");
      await bubble.waitFor({ state: "hidden" });
    }

    // Ctrl+K and Cmd+K never open the link control: the shell owns them.
    await newLine(page);
    await page.keyboard.type("Link words");
    await body.locator("p", { hasText: "Link words" }).selectText();
    await page.keyboard.press("Control+k");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("textbox", { name: "Link address or page" })).toHaveCount(0);

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    const saved = (kb.writes[0].body as { body_md: string }).body_md;
    expect(saved).toContain("**Bold words**");
    expect(saved).toContain("*Italic words*");
    expect(saved).toContain("~~Strikethrough words~~");
    expect(saved).toContain("`Inline code words`");
  });

  test("[[ and the link control insert a link to a page", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);

    await newLine(page);
    await page.keyboard.type("See [[Holi");
    const picker = page.getByRole("listbox", { name: "Pages" });
    await expect(picker.getByRole("option", { name: /Holidays/ })).toBeVisible();
    // The page being edited is never offered.
    await expect(picker.getByRole("option", { name: /Refund policy/ })).toHaveCount(0);
    await page.keyboard.press("Enter");
    await expect(body.locator("a[href='/knowledge/pages/kp-b']", { hasText: "Holidays" })).toBeVisible();

    // The link control in the bubble menu searches the pages too.
    await newLine(page);
    await page.keyboard.type("Time off");
    await page.keyboard.press("Shift+Home");
    await page.getByRole("toolbar", { name: "Format the selection" }).getByRole("button", { name: "Link", exact: true }).click();
    await page.getByRole("textbox", { name: "Link address or page" }).fill("Holi");
    await page.getByRole("option", { name: /Holidays/ }).click();
    await expect(body.locator("a[href='/knowledge/pages/kp-b']", { hasText: "Time off" })).toBeVisible();

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    const saved = (kb.writes[0].body as { body_md: string }).body_md;
    expect(saved).toContain("See [Holidays](/knowledge/pages/kp-b)");
    expect(saved).toContain("[Time off](/knowledge/pages/kp-b)");

    // In the read view, the link opens the page in place.
    await page.locator("article a[href='/knowledge/pages/kp-b']").first().click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-b$/);
    await expect(page.getByRole("heading", { level: 2, name: "Holidays" })).toBeVisible();
  });

  test("the block handle moves a block", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Alpha\n\nBeta\n\nGamma") });
    const body = await openEditor(page);
    const beta = body.locator("p", { hasText: "Beta" });
    const alpha = body.locator("p", { hasText: "Alpha" });
    // The handle loads with the edit, as its own chunk.
    await page.locator(".kb-drag-handle").waitFor({ state: "attached" });
    await beta.hover();
    const grip = page.locator(".kb-drag-grip");
    await expect(grip).toBeVisible();
    await page.locator(".kb-drag-handle").dragTo(alpha, { targetPosition: { x: 2, y: 4 } });
    await expect(body.locator("p").first()).toHaveText("Beta");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    expect(kb.writes[0].body).toEqual({ body_md: "Beta\n\nAlpha\n\nGamma" });
  });

  test("a save with no edit writes nothing", async ({ page }) => {
    await setup(page);
    // `*` bullets and `__strong__` write back as `-` and `**`. An open and a
    // close with no edit must still write nothing.
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("* one\n* two\n\n__strong__ text") });
    await openEditor(page);
    await page.keyboard.press("ArrowUp");
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Discard your changes?" })).toHaveCount(0);
    expect(kb.writes).toEqual([]);
  });

  test("a member writes a page with each block, saves it, and reads the same layout", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("") });
    const body = await openEditor(page);
    await page.keyboard.type("# Returns");
    await page.keyboard.press("Enter");
    await page.keyboard.type("- Keep the receipt");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Ask the shop");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    const blocks = page.getByRole("listbox", { name: "Blocks" });
    await page.keyboard.type("/Note callout");
    await expect(blocks.getByRole("option").first()).toHaveText("Note callout");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Refunds take five days.");
    // Enter on an empty line leaves the callout, as it leaves a quote.
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Read [[Holi");
    await expect(page.getByRole("option", { name: /Holidays/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.type("/Table");
    await expect(blocks.getByRole("option").first()).toHaveText("Table");
    await page.keyboard.press("Enter");
    await page.keyboard.type("Item");

    const tags = async (root: import("@playwright/test").Locator) =>
      root.evaluate((el) => Array.from(el.querySelectorAll("h2,ul,li,.kb-callout,table,a")).map((n) => n.tagName + (n.getAttribute("href") ?? "")));
    const editTags = await tags(body);
    expect(editTags).toEqual(expect.arrayContaining(["H2", "UL", "LI", "DIV", "TABLE", "A/knowledge/pages/kp-b"]));

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "Refund policy" })).toBeVisible();
    expect(kb.writes[0].body).toEqual({
      body_md: [
        "# Returns",
        "- Keep the receipt\n- Ask the shop",
        "> [!NOTE]\n> Refunds take five days.",
        "Read [Holidays](/knowledge/pages/kp-b)",
        "| Item |  |  |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |",
      ].join("\n\n"),
    });

    // The read view draws the same blocks, in the same order, as the editor did.
    const read = page.locator("article .kb-editor");
    await expect(read.getByRole("heading", { name: "Returns" })).toBeVisible();
    expect(await tags(read)).toEqual(editTags);
    const h1 = await page.evaluate(() =>
      Array.from(document.querySelectorAll("h1")).filter((h) => h.getClientRects().length > 0).map((h) => h.textContent),
    );
    expect(h1).toEqual(["Knowledge"]);
  });

  test("the Markdown field stays as the fallback (§16 Q7)", async ({ page }) => {
    await setup(page);
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    await openEditor(page);
    await page.getByRole("button", { name: "Markdown", exact: true }).click();
    const field = page.getByRole("textbox", { name: "Page body in Markdown" });
    await expect(field).toHaveValue("Start.");
    await field.fill("# From Markdown\n\n> [!TIP]\n> Typed as text.");
    await page.getByRole("button", { name: "Markdown", exact: true }).click();
    const body = page.getByRole("textbox", { name: "Page body" });
    await expect(body.locator("h2[data-md-level='1']", { hasText: "From Markdown" })).toBeVisible();
    await expect(body.locator(".kb-callout[data-callout='tip']")).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(1);
    expect(kb.writes[0].body).toEqual({ body_md: "# From Markdown\n\n> [!TIP]\n> Typed as text." });
  });

  test("typing on a long page parses nothing, and Save still writes the last key", async ({ page }) => {
    test.setTimeout(90_000);
    await setup(page);
    // About 20 KB of body: before the fix round each key cost a parse and a
    // write of all of it.
    const long = Array.from({ length: 300 }, (_, i) => `Paragraph ${i} holds some words so the body is long.`).join("\n\n");
    const kb = await mockKnowledge(page, { level: "write", pages: pageWith(long) });
    await openEditor(page);
    type Work = { parses: number; writes: number };
    const work = () => page.evaluate(() => ({ ...(window as unknown as { __kbMarkdownWork: Work }).__kbMarkdownWork }));
    const before = await work();
    const started = Date.now();
    await page.keyboard.type(" and the last words");
    const typing = Date.now() - started;
    const after = await work();
    expect(after.parses - before.parses).toBe(0);
    expect(after.writes - before.writes).toBeLessThanOrEqual(1);
    expect(typing).toBeLessThan(3_000);
    // Save at once, inside the pause: the flush writes the last key.
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => kb.writes.length).toBe(1);
    expect((kb.writes[0].body as { body_md: string }).body_md.endsWith("is long. and the last words")).toBe(true);
  });

  test("Tab in the last cell of a table leaves the editor", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: pageWith("| a | b |\n| --- | --- |\n| 1 | 2 |") });
    const body = await openEditor(page);
    // The text of the cell that holds the caret, read from the editor's own
    // state. ProseMirror reads a click's caret on the next `selectionchange`,
    // so each step waits for the state, never for a fixed time.
    const caretCell = () =>
      body.evaluate((el) => {
        const ed = (el as unknown as { editor: { state: { selection: { $from: { node: (d: number) => { type: { name: string }; textContent: string }; depth: number } } } } }).editor;
        const $from = ed.state.selection.$from;
        for (let d = $from.depth; d > 0; d--) {
          const n = $from.node(d);
          if (n.type.name === "tableCell" || n.type.name === "tableHeader") return n.textContent;
        }
        return null;
      });
    const editorHasFocus = () => body.evaluate((el) => document.activeElement === el);
    await body.locator("th", { hasText: "a" }).click();
    await expect.poll(caretCell).toBe("a");
    for (const next of ["b", "1", "2"]) {
      await page.keyboard.press("Tab");
      await expect.poll(caretCell).toBe(next);
      await expect.poll(editorHasFocus).toBe(true);
    }
    await page.keyboard.press("Tab");
    await expect.poll(editorHasFocus).toBe(false);
    await expect(body.locator("tr")).toHaveCount(2);
  });

  test("the link control refuses a script address and says what to type", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);
    await newLine(page);
    await page.keyboard.type("Bad link");
    await body.locator("p", { hasText: "Bad link" }).selectText();
    await page.getByRole("toolbar", { name: "Format the selection" }).getByRole("button", { name: "Link", exact: true }).click();
    const field = page.getByRole("textbox", { name: "Link address or page" });
    await field.fill("javascript:alert(1)");
    await field.press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: "Use an address that starts with https://" })).toBeVisible();
    await expect(field).toBeVisible();
    await expect(body.locator("a", { hasText: "Bad link" })).toHaveCount(0);
  });

  test("the [[ picker names its open list and its active row on the editor", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: pageWith("Start.") });
    const body = await openEditor(page);
    await newLine(page);
    await page.keyboard.type("[[Holi");
    await expect(page.getByRole("listbox", { name: "Pages" }).getByRole("option", { name: /Holidays/ })).toBeVisible();
    await expect(body).toHaveAttribute("aria-expanded", "true");
    await expect(body).toHaveAttribute("aria-controls", "kb-page-menu");
    await expect(body).toHaveAttribute("aria-activedescendant", "kb-page-menu-option-0");
    await page.keyboard.press("Escape");
    await expect(body).toHaveAttribute("aria-expanded", "false");
  });

  test("a linked image keeps its link in the read view", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: pageWith("[![badge](https://img.example/b.svg)](https://ci.example)") });
    await page.goto("/knowledge/pages/kp-a");
    const link = page.locator("article a[href='https://ci.example']");
    await expect(link).toBeVisible({ timeout: 30_000 });
    await expect(link.getByText("Image from img.example")).toBeVisible();
  });

  test("a remote image in a page loads only on a click", async ({ page }) => {
    await setup(page);
    await mockKnowledge(page, { level: "write", pages: pageWith("![A chart](https://example.com/chart.png)") });
    await page.goto("/knowledge/pages/kp-a");
    const article = page.locator("article");
    await expect(article.getByText("Image from example.com")).toBeVisible({ timeout: 30_000 });
    await expect(article.locator("img[src]")).toHaveCount(0);
    await article.getByRole("button", { name: "Load image from example.com" }).click();
    await expect(article.locator("img[src='https://example.com/chart.png']")).toHaveCount(1);
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
    await expect(page.getByRole("textbox", { name: "Page body" })).toBeVisible({ timeout: 15_000 });
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
