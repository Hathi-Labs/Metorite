import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

test.describe.configure({ timeout: 150_000 });

/**
 * The Review view of the assistant's proposals (WS-61 KB-5b): the rail entry
 * with its count, the diff of an edit, the draft of a new page, Apply edit,
 * Publish and Reject, the 409, and the marks on the page itself.
 *
 * The BFF at `/api/knowledge/**` is a small mock that keeps state, with its
 * own copy as the other Knowledge specs have.
 */

const SPACE_ID = "ks-company";
const EDIT_ID = "e0000000-0000-4000-8000-000000000001";
const DRAFT_ID = "d0000000-0000-4000-8000-000000000002";
const PAGE_ID = "kp-refunds";
const CURRENT = "Refunds take ten days. Ask your lead for a form.";
const PROPOSED = "Refunds take five days. Ask your lead for a form.";

type Opts = { proposals?: Array<"edit" | "page">; conflict?: boolean };

async function setup(page: Page, opts: Opts = {}) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, {
    "auth/me": {
      authenticated: true,
      email: "asha@example.com",
      name: "Asha Rao",
      is_admin: false,
      features: ["knowledge"],
      permissions: [],
      capabilities: [],
      roles: ["member"],
      organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
    },
  });
  const now = new Date().toISOString();
  const kinds = opts.proposals ?? ["edit", "page"];
  const all = [
    {
      id: DRAFT_ID, kind: "page", page_id: DRAFT_ID, space_id: SPACE_ID, page_title: "Onboarding checklist",
      base_version: null, title: "Onboarding checklist", body_md: "Day one: **laptop** and badge.",
      summary: "A checklist for new hires.", proposer_email: "asha@example.com", via: "chat", status: "pending", created_at: now,
    },
    {
      id: EDIT_ID, kind: "edit", page_id: PAGE_ID, space_id: SPACE_ID, page_title: "Refund policy",
      base_version: 1, title: "Refund policy", body_md: PROPOSED,
      summary: "Refunds now take five days.", proposer_email: "asha@example.com", via: "chat", status: "pending", created_at: now,
    },
  ].filter((p) => kinds.includes(p.kind as "edit" | "page"));
  const state = { pending: all, accepts: [] as string[], rejects: [] as string[] };
  const json = (status: number, payload: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });
  const summary = (id: string, title: string, pageState: string) => ({
    id, space_id: SPACE_ID, parent_id: null, position: 0, title, icon: null, state: pageState,
    author_email: "asha@example.com", reviewers: [], review_at: null, source_ref: null, published_at: null,
    archived_at: null, history_from: null, archived_from_state: null, updated_at: now, created_at: now,
  });
  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\/knowledge\//, "");
    if (path === "spaces") {
      return route.fulfill(json(200, { spaces: [{ id: SPACE_ID, slug: "company", name: "Company", purpose: "How we work.",
        icon: "", kind: "company", owner_email: null, archived_at: null, created_at: now, level: "write", page_count: 2 }] }));
    }
    if (path === "pages" && req.method() === "GET") {
      return route.fulfill(json(200, { pages: [summary(PAGE_ID, "Refund policy", "published")] }));
    }
    if (path.startsWith("proposals") && req.method() === "GET") {
      return route.fulfill(json(200, { proposals: state.pending }));
    }
    const decision = /^proposals\/([^/]+)\/(accept|reject)$/.exec(path);
    if (decision && req.method() === "POST") {
      if (opts.conflict) {
        return route.fulfill(json(409, { detail: "The page changed after the proposal was made. Ask for a new one." }));
      }
      const [, id, verb] = decision;
      (verb === "accept" ? state.accepts : state.rejects).push(id);
      state.pending = state.pending.filter((p) => p.id !== id);
      return route.fulfill(json(200, { id, status: verb === "accept" ? "accepted" : "rejected" }));
    }
    if (path === `pages/${PAGE_ID}`) {
      return route.fulfill(json(200, { ...summary(PAGE_ID, "Refund policy", "published"), body_md: CURRENT, origin: null,
        level: "write", can_write: true, links: [], versions: [] }));
    }
    if (path === `pages/${DRAFT_ID}`) {
      return route.fulfill(json(200, { ...summary(DRAFT_ID, "Onboarding checklist", "draft"), body_md: "Day one: **laptop** and badge.",
        origin: { kind: "agent_proposal", via: "chat", proposer: "asha@example.com", summary: "x" },
        level: "write", can_write: true, links: [], versions: [] }));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return state;
}

test("the rail shows Review with the count of pending proposals", async ({ page }) => {
  await setup(page);
  await page.goto("/knowledge");
  const nav = page.getByRole("navigation", { name: "Knowledge spaces" });
  await expect(nav.getByRole("button", { name: /^Review/ })).toBeVisible({ timeout: 30_000 });
  await expect(nav.getByRole("button", { name: /^Review/ })).toContainText("2");
});

test("no pending proposal means no Review entry", async ({ page }) => {
  await setup(page, { proposals: [] });
  await page.goto("/knowledge");
  const nav = page.getByRole("navigation", { name: "Knowledge spaces" });
  await expect(nav.getByRole("button", { name: "Spaces" })).toBeVisible({ timeout: 30_000 });
  await expect(nav.getByRole("button", { name: /^Review/ })).toHaveCount(0);
  await page.goto("/knowledge/review");
  await expect(page.getByText("Nothing waits for your review.")).toBeVisible();
});

test("an edit shows a diff with marked words, and Apply edit accepts it", async ({ page }) => {
  const kb = await setup(page);
  await page.goto(`/knowledge/review?p=${EDIT_ID}`);
  const detail = page.getByRole("article", { name: "Proposal: Refund policy" });
  await expect(detail.getByText("Refunds now take five days.")).toBeVisible({ timeout: 30_000 });
  await expect(detail.locator(".kb-diff-added")).toContainText("five");
  await expect(detail.locator(".kb-diff-removed")).toContainText("ten");
  await detail.getByRole("button", { name: "Apply edit" }).click();
  await expect.poll(() => kb.accepts).toEqual([EDIT_ID]);
  await expect(page.getByText("Applied the edit to Refund policy")).toBeVisible();
  // The edit left the list, and the page proposal is open next.
  await expect(page.locator(`[data-proposal-row="${EDIT_ID}"]`)).toHaveCount(0);
  await expect(page.getByRole("article", { name: "Proposal: Onboarding checklist" })).toBeVisible();
});

test("a new page shows its draft, and Publish accepts it", async ({ page }) => {
  const kb = await setup(page);
  await page.goto(`/knowledge/review?p=${DRAFT_ID}`);
  const detail = page.getByRole("article", { name: "Proposal: Onboarding checklist" });
  await expect(detail.getByText("laptop")).toBeVisible({ timeout: 30_000 });
  await expect(detail.locator(".kb-diff-added")).toHaveCount(0);
  await detail.getByRole("button", { name: "Publish" }).click();
  await expect.poll(() => kb.accepts).toEqual([DRAFT_ID]);
  await expect(page.getByText("Published Onboarding checklist")).toBeVisible();
});

test("Reject posts reject and removes the proposal", async ({ page }) => {
  const kb = await setup(page);
  await page.goto(`/knowledge/review?p=${EDIT_ID}`);
  const detail = page.getByRole("article", { name: "Proposal: Refund policy" });
  await detail.getByRole("button", { name: "Reject" }).click({ timeout: 30_000 });
  await expect.poll(() => kb.rejects).toEqual([EDIT_ID]);
  await expect(page.locator(`[data-proposal-row="${EDIT_ID}"]`)).toHaveCount(0);
});

test("a 409 shows the server's words and keeps the proposal", async ({ page }) => {
  const kb = await setup(page, { conflict: true });
  await page.goto(`/knowledge/review?p=${EDIT_ID}`);
  const detail = page.getByRole("article", { name: "Proposal: Refund policy" });
  await detail.getByRole("button", { name: "Apply edit" }).click({ timeout: 30_000 });
  await expect(detail.getByRole("alert")).toContainText("The page changed after the proposal was made.");
  await expect(detail.getByRole("alert")).toContainText("propose it again");
  await expect(page.locator(`[data-proposal-row="${EDIT_ID}"]`)).toBeVisible();
  expect(kb.accepts).toEqual([]);
  await expect(detail.getByRole("button", { name: "Reject" })).toBeEnabled();
});

test("a draft from the assistant carries the badge and a Review link", async ({ page }) => {
  await setup(page);
  await page.goto(`/knowledge/pages/${DRAFT_ID}`);
  const mark = page.locator("[data-agent-draft]");
  await expect(mark.getByText("Proposed by the assistant")).toBeVisible({ timeout: 30_000 });
  await mark.getByRole("link", { name: "Review" }).click();
  await expect(page).toHaveURL(new RegExp(`/knowledge/review\\?p=${DRAFT_ID}`));
});

test("a published page with a pending edit shows the notice", async ({ page }) => {
  await setup(page);
  await page.goto(`/knowledge/pages/${PAGE_ID}`);
  const notice = page.locator("[data-pending-edit]");
  await expect(notice).toContainText("The assistant proposed an edit", { timeout: 30_000 });
  await notice.getByRole("link", { name: "Review" }).click();
  await expect(page).toHaveURL(new RegExp(`/knowledge/review\\?p=${EDIT_ID}`));
});
