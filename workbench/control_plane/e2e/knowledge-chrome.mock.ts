import type { Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * A stateful mock of the Knowledge BFF for the page chrome (WS-61 KB-2d).
 *
 * It answers the reads and the writes that the chrome uses: the spaces, the
 * pages of a space (with `state=archived`), one page with its versions, and
 * move, archive and restore. Each write goes into `writes`, so a test can
 * read what reached the BFF. The real gateway half is
 * `tests/unit/test_kb_pages_r8.py`.
 */

export const COMPANY = "ks-company";
export const OPS = "ks-ops";

export type Level = "read" | "write" | "manage";

export interface MockVersion {
  version: number;
  title: string;
  reason: string;
  author: string;
  /** Minutes before now. */
  ago: number;
}

export interface MockPage {
  id: string;
  space_id: string;
  parent_id: string | null;
  title: string;
  body_md: string;
  state: "draft" | "published" | "archived";
  archived_from_state?: string | null;
  author: string;
  versions: MockVersion[];
}

export interface MockOpts {
  /** The member's level on each space. */
  levels: Partial<Record<string, Level>>;
  pages: MockPage[];
}

export function member() {
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

/** Sign the member in, with the shell bar on, as the other Knowledge specs do. */
export async function signIn(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member() });
}

export async function mockKnowledge(page: Page, opts: MockOpts) {
  const writes: Array<{ method: string; path: string; body: unknown }> = [];
  const pages = new Map<string, MockPage>(opts.pages.map((p) => [p.id, structuredClone(p)]));
  const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
  const level = (spaceId: string): Level => opts.levels[spaceId] ?? "read";

  const summary = (p: MockPage) => ({
    id: p.id,
    space_id: p.space_id,
    parent_id: p.parent_id,
    position: 0,
    title: p.title,
    state: p.state,
    author_email: p.author,
    reviewers: [],
    review_at: p.state === "published" ? "2027-04-14T00:00:00Z" : null,
    source_ref: null,
    published_at: null,
    archived_at: p.state === "archived" ? minutesAgo(5) : null,
    history_from: null,
    archived_from_state: p.state === "archived" ? p.archived_from_state ?? "published" : null,
    updated_at: minutesAgo(p.versions[p.versions.length - 1]?.ago ?? 0),
    created_at: minutesAgo(p.versions[0]?.ago ?? 0),
  });
  const detail = (p: MockPage) => ({
    ...summary(p),
    body_md: p.body_md,
    origin: null,
    level: level(p.space_id),
    can_write: level(p.space_id) !== "read",
    versions: [...p.versions].reverse().map((v) => ({
      version: v.version,
      title: v.title,
      author_email: v.author,
      reason: v.reason,
      via: "web",
      created_at: minutesAgo(v.ago),
    })),
    links: [],
  });
  const version = (p: MockPage, reason: string) =>
    p.versions.push({ version: p.versions.length + 1, title: p.title, reason, author: "asha@example.com", ago: 0 });
  const written = (p: MockPage) => ({ ...summary(p), body_md: p.body_md, origin: null, version: p.versions.length });
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
      const count = (id: string) => [...pages.values()].filter((p) => p.space_id === id && p.state !== "archived").length;
      const space = (id: string, slug: string, name: string, purpose: string) => ({
        id, slug, name, purpose, icon: "", kind: "team", owner_email: null, archived_at: null,
        created_at: minutesAgo(1000), level: level(id), page_count: count(id),
      });
      return route.fulfill(json(200, {
        spaces: [
          { ...space(COMPANY, "company", "Company", "Products, services and policies."), kind: "company" },
          space(OPS, "ops", "Operations", "How the shop floor runs."),
        ],
      }));
    }
    if (path === "pages" && method === "GET") {
      const space = url.searchParams.get("space");
      const state = url.searchParams.get("state");
      const rows = [...pages.values()].filter(
        (p) => p.space_id === space && (state ? p.state === state : p.state !== "archived"),
      );
      return route.fulfill(json(200, { pages: rows.map(summary) }));
    }
    const m = /^pages\/([^/]+)(?:\/(move|archive|restore))?$/.exec(path);
    const found = m ? pages.get(decodeURIComponent(m[1])) : undefined;
    if (!m || !found) return route.fulfill(json(404, { detail: "Not found" }));
    if (method === "GET") return route.fulfill(json(200, detail(found)));
    if (m[2] === "move") {
      if (level(found.space_id) === "read") return route.fulfill(json(403, { detail: "Moving needs a write grant." }));
      if (body.space_id) found.space_id = body.space_id;
      if ("parent_id" in body) found.parent_id = body.parent_id;
      version(found, "move");
      return route.fulfill(json(200, written(found)));
    }
    if (m[2] === "archive" || m[2] === "restore") {
      if (level(found.space_id) !== "manage") {
        return route.fulfill(json(403, { detail: "This needs manage on the space." }));
      }
      if (m[2] === "archive") {
        found.archived_from_state = found.state;
        found.state = "archived";
      } else {
        found.state = (found.archived_from_state as MockPage["state"]) ?? "draft";
        found.archived_from_state = null;
      }
      version(found, m[2]);
      return route.fulfill(json(200, written(found)));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { writes, pages };
}

/** A body with three headings, long enough to scroll. */
export function longBody(): string {
  const para = (n: number) =>
    Array.from({ length: 6 }, (_, i) => `Line ${n}.${i + 1} of the policy, with enough words to wrap.`).join(" ");
  return [
    "# Scope",
    "",
    para(1),
    "",
    para(2),
    "",
    "## Who may ask",
    "",
    para(3),
    "",
    para(4),
    "",
    "## How to ask",
    "",
    para(5),
    "",
    para(6),
    "",
    para(7),
    "",
    para(8),
  ].join("\n");
}

/** A tree in Company: Handbook > Policies > Travel > Refunds, and a page in Ops. */
export function chromePages(): MockPage[] {
  const v = (title: string, ...authors: Array<[string, number]>): MockVersion[] =>
    authors.map(([author, ago], i) => ({ version: i + 1, title, reason: i === 0 ? "create" : "edit", author, ago }));
  return [
    { id: "kp-h", space_id: COMPANY, parent_id: null, title: "Handbook", body_md: "Start here.",
      state: "published", author: "asha@example.com", versions: v("Handbook", ["asha@example.com", 600]) },
    { id: "kp-p", space_id: COMPANY, parent_id: "kp-h", title: "Policies", body_md: "All policies.",
      state: "published", author: "asha@example.com", versions: v("Policies", ["asha@example.com", 500]) },
    { id: "kp-t", space_id: COMPANY, parent_id: "kp-p", title: "Travel", body_md: "Travel rules.",
      state: "published", author: "asha@example.com", versions: v("Travel", ["asha@example.com", 400]) },
    { id: "kp-r", space_id: COMPANY, parent_id: "kp-t", title: "Refunds", body_md: longBody(),
      state: "published", author: "asha@example.com",
      versions: v("Refunds", ["asha@example.com", 300], ["ravi@example.com", 120], ["meera@example.com", 3]) },
    { id: "kp-one", space_id: COMPANY, parent_id: null, title: "Short note", body_md: "# Only heading\n\nOne line.",
      state: "published", author: "asha@example.com", versions: v("Short note", ["asha@example.com", 30]) },
    { id: "kp-old", space_id: COMPANY, parent_id: null, title: "Old process", body_md: "We did it this way.",
      state: "archived", archived_from_state: "published", author: "ravi@example.com",
      versions: v("Old process", ["ravi@example.com", 900]) },
    { id: "kp-ops", space_id: OPS, parent_id: null, title: "Shifts", body_md: "Three shifts.",
      state: "published", author: "asha@example.com", versions: v("Shifts", ["asha@example.com", 800]) },
  ];
}
