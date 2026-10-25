import { expect, test, type Page } from "@playwright/test";

import { busyPages, member, setupHome } from "./knowledgeHomeMock";

/**
 * The Sources view (WS-61 KC-1b, `knowledge_curation.md` §7.1).
 *
 * The BFF is the stateful mock of `knowledgeHomeMock.ts`, and this file adds
 * the source routes: the list, the tree of one drive, the connect and the
 * disconnect. The consent page is a stub of accounts.google.com. No live
 * Google test runs here. The real gateway half is
 * `tests/unit/test_kc_sources_r8.py`.
 */

type MockSource = {
  id: string;
  owner_email: string;
  state: "discovering" | "ready" | "failed" | "needs_reconnect" | "disconnected";
  account_email: string;
  node_count: number;
};

type MockNode = {
  ext_id: string;
  parent_ext_id: string | null;
  kind: "folder" | "file";
  name: string;
  mime: string | null;
  size: number | null;
  web_url: string | null;
  explored: boolean;
  has_children: boolean;
};

const LONG_NAME = "Board pack final FINAL v7 reviewed by finance and legal and the audit committee 2026 Q3.xlsx";

const NODES: MockNode[] = [
  { ext_id: "f-fin", parent_ext_id: null, kind: "folder", name: "Finance", mime: null, size: null, web_url: null, explored: true, has_children: true },
  { ext_id: "f-old", parent_ext_id: null, kind: "folder", name: "Old scans", mime: null, size: null, web_url: null, explored: false, has_children: false },
  {
    ext_id: "x-budget", parent_ext_id: null, kind: "file", name: "Budget.xlsx",
    mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size: 20480,
    web_url: "https://docs.google.com/spreadsheets/d/1/edit", explored: true, has_children: false,
  },
  {
    ext_id: "x-long", parent_ext_id: null, kind: "file", name: LONG_NAME, mime: "application/pdf", size: 3_145_728,
    web_url: "https://drive.google.com/file/d/2/view", explored: true, has_children: false,
  },
  {
    ext_id: "x-evil", parent_ext_id: null, kind: "file", name: "Hostile.pdf", mime: "application/pdf", size: 10,
    web_url: "https://evil.example/steal", explored: true, has_children: false,
  },
  { ext_id: "f-q3", parent_ext_id: "f-fin", kind: "folder", name: "Q3 close", mime: null, size: null, web_url: null, explored: true, has_children: false },
];

interface SourceOpts {
  manager?: boolean;
  off?: boolean;
  sources?: MockSource[];
  /** Read as discovering until this many list reads, then ready. */
  discoverReads?: number;
  connectUrl?: string;
  connectStatus?: number;
  connectDetail?: string;
  deleteFails?: boolean;
  truncated?: boolean;
}

const mine = (over: Partial<MockSource> = {}): MockSource => ({
  id: "s-1", owner_email: "asha@example.com", state: "ready", account_email: "asha@example.com", node_count: 1204, ...over,
});

async function setupSources(page: Page, opts: SourceOpts = {}) {
  const manager = opts.manager ?? false;
  await setupHome(page, { level: "write", pages: busyPages() });
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: { ...member(), capabilities: manager ? ["knowledge:manage"] : [] } }),
  );
  const sources: MockSource[] = (opts.sources ?? []).map((s) => ({ ...s }));
  const calls: Array<{ method: string; path: string }> = [];
  const state = { listReads: 0 };
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  const now = new Date().toISOString();
  const row = (s: MockSource) => ({
    id: s.id, provider: "google_drive", kind: "drive", owner_email: s.owner_email, state: s.state,
    account_email: s.account_email, node_count: s.node_count, discovered_at: now, last_error: null,
    created_at: now, updated_at: now,
  });

  await page.route("**/api/knowledge/sources**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    calls.push({ method: req.method(), path: path + url.search });
    if (opts.off) return route.fulfill(json(404, { detail: "Not Found" }));

    if (path === "sources" && req.method() === "GET") {
      state.listReads += 1;
      if (opts.discoverReads !== undefined) {
        for (const s of sources) {
          s.state = state.listReads >= opts.discoverReads ? "ready" : "discovering";
          s.node_count = state.listReads >= opts.discoverReads ? 1204 : 40 * state.listReads;
        }
      }
      return route.fulfill(json(200, { sources: sources.map(row) }));
    }
    if (path === "sources/google_drive/connect" && req.method() === "POST") {
      if (opts.connectStatus && opts.connectStatus !== 200) {
        return route.fulfill(json(opts.connectStatus, { detail: opts.connectDetail ?? "Not Found" }));
      }
      return route.fulfill(json(200, { url: opts.connectUrl ?? "https://accounts.google.com/o/oauth2/v2/auth?x=1" }));
    }
    const tree = /^sources\/([^/]+)\/tree$/.exec(path);
    if (tree && req.method() === "GET") {
      const parent = url.searchParams.get("parent");
      const nodes = NODES.filter((n) => n.parent_ext_id === parent).map((n) => ({
        ...n, drive_id: "d1", modified_at: now,
      }));
      return route.fulfill(json(200, { source: tree[1], parent, nodes, truncated: opts.truncated === true && parent === null }));
    }
    const del = /^sources\/([^/]+)$/.exec(path);
    if (del && req.method() === "DELETE") {
      if (opts.deleteFails) return route.fulfill(json(500, { detail: "The disconnect failed." }));
      const found = sources.find((s) => s.id === del[1]);
      if (!found) return route.fulfill(json(404, { detail: "Source not found." }));
      found.state = "disconnected";
      return route.fulfill(json(200, row(found)));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  // The consent page: a stub, so the test can see the browser arrive.
  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<html><body><h1>Google consent stub</h1></body></html>" }),
  );
  return { sources, calls, state };
}

const railRow = (page: Page) => page.getByRole("navigation", { name: "Knowledge spaces" }).getByRole("button", { name: /^Sources/ });
const card = (page: Page, id = "s-1") => page.locator(`[data-source-id="${id}"]`);
const BOOT = { timeout: 60_000 };

test.describe("Knowledge sources", () => {
  test("a member connects Drive, returns, and watches the read finish", async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const mock = await setupSources(page, { sources: [] });

    await page.goto("/knowledge/sources");
    await expect(page.getByRole("heading", { level: 2, name: "Sources" })).toBeVisible(BOOT);
    await expect(railRow(page)).toBeVisible();
    await page.getByRole("button", { name: "Connect Google Drive" }).click();
    await expect(page.getByRole("heading", { name: "Google consent stub" })).toBeVisible({ timeout: 30_000 });
    expect(mock.calls.filter((c) => c.method === "POST" && c.path === "sources/google_drive/connect")).toHaveLength(1);

    // The return: a drive now exists, and is being read.
    const again = await setupSources(page, { sources: [mine({ state: "discovering", node_count: 0 })], discoverReads: 3 });
    await page.goto("/knowledge?source_connected=google_drive");
    await expect(page.getByText("Google Drive connected. Metorite is reading your folders.").first()).toBeVisible(BOOT);
    await expect(page).toHaveURL(/\/knowledge\/sources$/, { timeout: 30_000 });
    expect(page.url()).not.toContain("?");
    await expect(card(page).getByText("Reading your folders").first()).toBeVisible({ timeout: 30_000 });
    await expect(card(page).getByText("Connected", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(card(page)).toContainText("1,204 items");

    // At ready the list calls stop.
    const reads = again.calls.filter((c) => c.path === "sources").length;
    await page.waitForTimeout(7_000);
    expect(again.calls.filter((c) => c.path === "sources").length).toBe(reads);

    // A reload does not repeat the toast.
    await page.reload();
    await expect(card(page)).toBeVisible(BOOT);
    await expect(page.getByText("Google Drive connected. Metorite is reading your folders.")).toHaveCount(0);
  });

  test("the tree: folders first, one load per folder, honest rows, safe links", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const mock = await setupSources(page, { sources: [mine()] });
    await page.goto("/knowledge/sources");

    const tree = page.getByRole("tree");
    await expect(tree).toBeVisible(BOOT);
    const names = await tree.getByRole("treeitem").locator("[title]").evaluateAll((els) => els.map((e) => e.getAttribute("title")));
    expect(names.slice(0, 2)).toEqual(["Finance", "Old scans"]);

    await expect(card(page)).toContainText("asha@example.com");
    await expect(card(page)).toContainText("Connected");
    await expect(card(page)).toContainText("1,204 items");
    await expect(card(page)).toContainText("Read ");

    // Expand once, close, expand again: one request for the children.
    const open = tree.getByRole("button", { name: "Open Finance" });
    await open.click();
    await expect(tree.getByText("Q3 close")).toBeVisible();
    await tree.getByRole("button", { name: "Close Finance" }).click();
    await tree.getByRole("button", { name: "Open Finance" }).click();
    await expect(tree.getByText("Q3 close")).toBeVisible();
    expect(mock.calls.filter((c) => c.path.includes("tree?parent=f-fin"))).toHaveLength(1);

    // A folder that discovery did not reach: no chevron.
    const old = tree.getByRole("treeitem").filter({ hasText: "Old scans" });
    await expect(old).toContainText("Not yet explored");
    await expect(old.getByRole("button")).toHaveCount(0);

    // A file: type, size, and a safe link.
    const budget = tree.getByRole("treeitem").filter({ hasText: "Budget.xlsx" });
    await expect(budget).toContainText("Sheet");
    await expect(budget).toContainText("20 KB");
    const link = budget.getByRole("link", { name: "Open Budget.xlsx in Google Drive" });
    await expect(link).toHaveAttribute("href", "https://docs.google.com/spreadsheets/d/1/edit");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);

    // A link to any other host is not drawn.
    await expect(tree.getByRole("treeitem").filter({ hasText: "Hostile.pdf" }).getByRole("link")).toHaveCount(0);

    // A long name is cut, and the full name is in the title.
    await expect(tree.locator(`[title="${LONG_NAME}"]`)).toHaveCount(1);

    // No file text anywhere in the card.
    const words = (await card(page).innerText()).toLowerCase();
    expect(words).not.toMatch(/\b(text|content|body)\b/);
  });

  test("a cut tree says so", async ({ page }) => {
    await setupSources(page, { sources: [mine()], truncated: true });
    await page.goto("/knowledge/sources");
    await expect(page.getByText(/^Showing the first \d+ items\. More items exist\.$/)).toBeVisible(BOOT);
  });

  test("disconnect asks first, and Cancel sends nothing", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const mock = await setupSources(page, { sources: [mine()] });
    await page.goto("/knowledge/sources");
    await expect(card(page)).toBeVisible(BOOT);

    await card(page).getByRole("button", { name: /^Disconnect/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Gmail");
    await expect(dialog).toContainText("stays connected");
    await dialog.locator("[data-cancel]").click();
    await expect(dialog).toHaveCount(0);
    expect(mock.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    await card(page).getByRole("button", { name: /^Disconnect/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(page.getByText("Disconnected asha@example.com").first()).toBeVisible({ timeout: 15_000 });
    await expect(card(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Connect Google Drive" })).toBeVisible();
    expect(mock.calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  test("a failed disconnect keeps the card and says why", async ({ page }) => {
    await setupSources(page, { sources: [mine()], deleteFails: true });
    await page.goto("/knowledge/sources");
    await expect(card(page)).toBeVisible(BOOT);
    await card(page).getByRole("button", { name: /^Disconnect/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(page.getByText("Could not disconnect the drive").first()).toBeVisible({ timeout: 15_000 });
    await expect(card(page)).toBeVisible();
  });

  test("reconnect: the owner can, another member's card waits, a manager sees the owner", async ({ page }) => {
    test.setTimeout(120_000);
    await setupSources(page, {
      manager: true,
      sources: [
        mine({ id: "s-1", state: "needs_reconnect" }),
        { id: "s-2", owner_email: "ravi@example.com", state: "needs_reconnect", account_email: "ravi@example.com", node_count: 10 },
        { id: "s-3", owner_email: "ravi@example.com", state: "failed", account_email: "ravi.work@example.com", node_count: 3 },
      ],
    });
    await page.goto("/knowledge/sources");
    await expect(card(page, "s-1")).toBeVisible(BOOT);

    await expect(card(page, "s-1").getByRole("button", { name: "Reconnect" })).toBeVisible();
    await expect(card(page, "s-1")).toContainText("Google stopped the access. Reconnect to read this drive again.");
    await expect(card(page, "s-1")).toContainText("Owner: You");

    await expect(card(page, "s-2")).toContainText("Waiting for ravi@example.com to reconnect.");
    await expect(card(page, "s-2").getByRole("button", { name: "Reconnect" })).toHaveCount(0);
    await expect(card(page, "s-2")).toContainText("Owner: ravi@example.com");
    await expect(card(page, "s-2").getByRole("button", { name: /^Disconnect/ })).toBeVisible();
    await expect(card(page, "s-3").getByRole("button", { name: "Reconnect" })).toHaveCount(0);

    await card(page, "s-1").getByRole("button", { name: "Reconnect" }).click();
    await expect(page.getByRole("heading", { name: "Google consent stub" })).toBeVisible({ timeout: 30_000 });
  });

  test("a failed drive of the viewer shows Reconnect and no provider text", async ({ page }) => {
    await setupSources(page, { sources: [mine({ state: "failed" })] });
    await page.goto("/knowledge/sources");
    await expect(card(page)).toContainText("Metorite could not read this drive.", BOOT);
    await expect(card(page).getByRole("button", { name: "Reconnect" })).toBeVisible();
  });

  test("with connected drives off the row hides and the view says so", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !/404|Failed to load resource/.test(m.text())) errors.push(m.text());
    });
    await setupSources(page, { off: true });
    await page.goto("/knowledge");
    await expect(page.getByRole("tab", { name: "Recently updated" })).toBeVisible(BOOT);
    await expect(railRow(page)).toHaveCount(0);
    await page.goto("/knowledge/sources");
    await expect(page.getByText("Connected drives are not on for your organization yet.")).toBeVisible(BOOT);
    await expect(page.locator('[data-type="error"]')).toHaveCount(0);
    expect(errors).toEqual([]);

    // A failed return is a plain sentence, and the address clears.
    await page.goto("/knowledge?source_error=access_denied");
    await expect(page.getByText("You did not allow access. Nothing was connected.").first()).toBeVisible(BOOT);
    await expect(page).toHaveURL(/\/knowledge\/sources$/, { timeout: 30_000 });
    await expect(page.getByText("access_denied")).toHaveCount(0);
  });

  test("the rail row shows when the list answers, even with no drive", async ({ page }) => {
    await setupSources(page, { sources: [] });
    await page.goto("/knowledge");
    await expect(railRow(page)).toBeVisible(BOOT);
    await railRow(page).click();
    await expect(page.getByText("Connect a drive so Knowledge can see your folders.")).toBeVisible(BOOT);
  });

  test("a hostile consent address goes nowhere, and a refused connect says why", async ({ page }) => {
    test.setTimeout(120_000);
    await setupSources(page, { sources: [], connectUrl: "https://evil.example/" });
    await page.goto("/knowledge/sources");
    await page.getByRole("button", { name: "Connect Google Drive" }).click();
    await expect(page.getByText("Could not start the connection. Try again.").first()).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/\/knowledge\/sources$/);

    const off = await page.context().newPage();
    await setupSources(off, { sources: [], connectStatus: 404 });
    await off.goto("/knowledge/sources");
    await off.getByRole("button", { name: "Connect Google Drive" }).click();
    await expect(off.getByText("Connecting Google Drive is not on for your organization yet.").first()).toBeVisible({ timeout: 30_000 });

    const bare = await page.context().newPage();
    await setupSources(bare, { sources: [], connectStatus: 503, connectDetail: "google_not_configured" });
    await bare.goto("/knowledge/sources");
    await bare.getByRole("button", { name: "Connect Google Drive" }).click();
    await expect(bare.getByText("Google Drive is not set up for Metorite yet. Ask your administrator.").first()).toBeVisible({ timeout: 30_000 });
  });
});
