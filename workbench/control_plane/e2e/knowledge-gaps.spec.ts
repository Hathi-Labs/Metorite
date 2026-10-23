import { expect, test, type Page } from "@playwright/test";

import { ME_EMAIL, SPACE_ID, busyPages, member, setupHome } from "./knowledgeHomeMock";

/**
 * The Gaps view (WS-61 KB-9a, `knowledge_base.md` §8.4 door 3).
 *
 * The BFF is the stateful mock of `knowledgeHomeMock.ts`, and this file adds
 * the gap routes and the create of a page. A manager of Knowledge sees the
 * Gaps row with its count. "Write a page" opens the New page form with the
 * question as the title, and the new page marks the gap answered. Dismiss
 * closes a gap. The link of the My Day card, `?write=<id>`, opens the form
 * on arrival. A member with no `knowledge:manage` gets no row, and an org
 * with gaps off reads "not on".
 *
 * The real gateway half is `tests/unit/test_kb_gaps_r8.py`.
 */

type MockGap = { id: string; question: string; ask_count: number; status: "open" | "answered" | "dismissed" };

async function setupGaps(page: Page, opts: { manager?: boolean; off?: boolean } = {}) {
  const manager = opts.manager ?? true;
  const kb = await setupHome(page, { level: manager ? "manage" : "write", pages: busyPages() });
  // After setupHome, so these routes answer first.
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: { ...member(), capabilities: manager ? ["knowledge:manage"] : [] } }),
  );
  const gaps: MockGap[] = [
    { id: "g-1", question: "How do we raise a credit note?", ask_count: 3, status: "open" },
    { id: "g-2", question: "Where is the travel expense policy?", ask_count: 2, status: "open" },
  ];
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  const now = new Date().toISOString();
  const row = (g: MockGap) => ({
    id: g.id, question: g.question, ask_count: g.ask_count, first_asked_at: now, last_asked_at: now,
    status: g.status, answered_page: null, asked_by_me: false, dismissed_at: null,
  });

  await page.route("**/api/knowledge/gaps**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\/knowledge\//, "");
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    calls.push({ method: req.method(), path, body });
    if (opts.off) return route.fulfill(json(404, { detail: "Not Found" }));
    if (path === "gaps") {
      return route.fulfill(json(200, {
        gaps: gaps.filter((g) => g.status === "open").map(row),
        scope: manager ? "org" : "mine",
      }));
    }
    const m = /^gaps\/([^/]+)\/(dismiss|answered)$/.exec(path);
    const gap = m ? gaps.find((g) => g.id === m[1]) : undefined;
    if (!m || !gap) return route.fulfill(json(404, { detail: "Not found" }));
    if (m[2] === "dismiss" && !manager) return route.fulfill(json(403, { detail: "Dismissing a question needs knowledge:manage." }));
    gap.status = m[2] === "dismiss" ? "dismissed" : "answered";
    return route.fulfill(json(200, row(gap)));
  });
  // The create of a page: setupHome answers reads only.
  await page.route("**/api/knowledge/pages", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") return route.fallback();
    const body = JSON.parse(req.postData() ?? "{}");
    calls.push({ method: "POST", path: "pages", body });
    const id = `kp-new-${kb.pages.size}`;
    kb.pages.set(id, {
      id, title: String(body.title), icon: null, state: "draft", author: ME_EMAIL, updated: Date.now(), versions: 1,
    });
    return route.fulfill(json(201, {
      id, space_id: body.space_id ?? SPACE_ID, parent_id: null, position: 0, title: body.title, icon: null,
      state: "draft", author_email: ME_EMAIL, reviewers: [], review_at: null, source_ref: null, published_at: null,
      archived_at: null, history_from: null, archived_from_state: null, updated_at: now, created_at: now,
      body_md: "", origin: null, version: 1,
    }));
  });
  return { gaps, calls };
}

const gapsRow = (page: Page) => page.getByRole("navigation", { name: "Knowledge spaces" }).getByRole("button", { name: /^Gaps/ });

test.describe("Knowledge gaps", () => {
  test("a manager lists the gaps, writes a page for one, and dismisses another", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const mock = await setupGaps(page);

    await page.goto("/knowledge");
    await expect(gapsRow(page)).toBeVisible({ timeout: 60_000 });
    await expect(gapsRow(page)).toContainText("2");
    await gapsRow(page).click();
    // A cold dev server compiles the route on the first visit.
    await expect(page).toHaveURL(/\/knowledge\/gaps$/, { timeout: 60_000 });
    await expect(page.getByRole("heading", { level: 2, name: "Gaps" })).toBeVisible({ timeout: 60_000 });
    const first = page.locator('[data-gap-id="g-1"]');
    await expect(first).toContainText("How do we raise a credit note?");
    await expect(first).toContainText("Asked by 3 people");

    // Write a page: the form starts with the question as the title.
    await first.getByRole("button", { name: /^Write a page for/ }).click();
    const dialog = page.getByRole("dialog", { name: "New page" });
    await expect(dialog.getByRole("textbox").first()).toHaveValue("How do we raise a credit note");
    await expect(dialog).toContainText("This page answers the question");
    await dialog.getByRole("button", { name: "Create page" }).click();
    await expect(page).toHaveURL(/\/knowledge\/pages\/kp-new-/, { timeout: 30_000 });
    await expect.poll(() => mock.calls.find((c) => c.path === "gaps/g-1/answered")?.body).toEqual({
      page_id: expect.stringMatching(/^kp-new-/),
    });
    expect(mock.calls.find((c) => c.path === "pages")?.body).toMatchObject({ title: "How do we raise a credit note" });

    // Dismiss the other one.
    await page.goto("/knowledge/gaps");
    await expect(page.locator('[data-gap-id="g-1"]')).toHaveCount(0, { timeout: 30_000 });
    await page.locator('[data-gap-id="g-2"]').getByRole("button", { name: /^Dismiss/ }).click();
    await expect(page.getByText("No open questions.")).toBeVisible({ timeout: 15_000 });
    expect(mock.calls.some((c) => c.method === "POST" && c.path === "gaps/g-2/dismiss")).toBe(true);
  });

  test("the My Day card's link opens the form for its gap", async ({ page }) => {
    await setupGaps(page);
    await page.goto("/knowledge/gaps?write=g-2");
    const dialog = page.getByRole("dialog", { name: "New page" });
    await expect(dialog.getByRole("textbox").first()).toHaveValue("Where is the travel expense policy", {
      timeout: 60_000,
    });
    await expect(page).toHaveURL(/\/knowledge\/gaps$/);
  });

  test("a member with no manage gets no Gaps row and no Dismiss", async ({ page }) => {
    await setupGaps(page, { manager: false });
    await page.goto("/knowledge");
    await expect(page.getByRole("tab", { name: "Recently updated" })).toBeVisible({ timeout: 60_000 });
    await expect(gapsRow(page)).toHaveCount(0);
    await page.goto("/knowledge/gaps");
    await expect(page.locator('[data-gap-id="g-1"]')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: /^Dismiss/ })).toHaveCount(0);
  });

  test("with gaps off the row hides and the view says so", async ({ page }) => {
    await setupGaps(page, { off: true });
    await page.goto("/knowledge");
    await expect(page.getByRole("tab", { name: "Recently updated" })).toBeVisible({ timeout: 60_000 });
    await expect(gapsRow(page)).toHaveCount(0);
    await page.goto("/knowledge/gaps");
    await expect(page.getByText("Gaps are not on for your organization yet.")).toBeVisible({ timeout: 30_000 });
  });
});
