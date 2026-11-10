import type { Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * A stateful mock of the Knowledge BFF for KB-2f (WS-61, `knowledge_base.md`
 * §21.7): the spaces, the tree, one page, the page icon, the favourites, the
 * recent lists and the search. It keeps the rules of the gateway that the
 * view depends on:
 *
 *   - a PATCH of only `icon` writes no version and keeps `updated_at`;
 *   - "Recently updated" orders by `updated_at`, pages by a cursor, and
 *     `mine=true` keeps the member's own pages;
 *   - a star is idempotent, and the favourites list newest star first.
 *
 * The real gateway half is `tests/unit/test_kb_home_r8.py`.
 */

export const SPACE_ID = "ks-company";
export const ME_EMAIL = "asha@example.com";

export type MockPage = {
  id: string;
  title: string;
  icon: string | null;
  state: "draft" | "published";
  author: string;
  updated: number;
  versions: number;
};

export interface HomeMockOpts {
  level: "read" | "write" | "manage";
  pages: MockPage[];
  /** The rows of one recent page. The client asks for 20. */
  favorites?: string[];
}

export function member() {
  return {
    authenticated: true,
    email: ME_EMAIL,
    name: "Asha Rao",
    is_admin: false,
    features: ["knowledge"],
    permissions: [],
    capabilities: [],
    roles: ["member"],
    organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
  };
}

/** The pages of a busy space: 23 pages, so "Recently updated" has a second page. */
export function busyPages(): MockPage[] {
  const titles = [
    "Refund policy", "Holidays", "Travel", "Onboarding checklist", "Security basics", "Expense claims",
    "Brand guide", "Office access", "Laptop setup", "Payroll dates", "Code of conduct", "Release notes",
    "Support rota", "Vendor list", "Pricing ladder", "Sales playbook", "Hiring process", "Leave policy",
    "Customer FAQ", "Incident runbook", "Meeting notes", "Product roadmap", "Quarterly goals",
  ];
  const icons: Record<number, string> = { 0: "💸", 1: "🏖️", 2: "✈️", 4: "🔐", 6: "🎨", 19: "🚨" };
  return titles.map((title, i) => ({
    id: `kp-${i}`,
    title,
    icon: icons[i] ?? null,
    state: i === 3 ? "draft" : "published",
    author: i % 3 === 0 ? ME_EMAIL : "ravi@example.com",
    updated: Date.now() - i * 3_600_000,
    versions: 2,
  }));
}

export async function setupHome(page: Page, opts: HomeMockOpts) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member() });
  const pages = new Map(opts.pages.map((p) => [p.id, { ...p }]));
  const stars: Array<{ id: string; at: number }> = (opts.favorites ?? []).map((id, i) => ({ id, at: Date.now() - i }));
  const writes: Array<{ method: string; path: string; body: unknown }> = [];
  const iso = (ms: number) => new Date(ms).toISOString();
  const visible = (p: MockPage) => p.state === "published" || p.author === ME_EMAIL;
  const summary = (p: MockPage) => ({
    id: p.id,
    space_id: SPACE_ID,
    parent_id: null,
    position: [...pages.keys()].indexOf(p.id),
    title: p.title,
    icon: p.icon,
    state: p.state,
    author_email: p.author,
    reviewers: [],
    review_at: null,
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: iso(p.updated),
    created_at: iso(p.updated - 86_400_000),
  });
  const listed = (p: MockPage) => ({ ...summary(p), space_name: "Company" });
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  const writable = opts.level !== "read";

  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    const method = req.method();
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    if (method !== "GET") writes.push({ method, path, body });

    if (path === "spaces") {
      return route.fulfill(json(200, {
        spaces: [
          { id: SPACE_ID, slug: "company", name: "Company", purpose: "Products, services, policies, and how we do things.",
            icon: "", kind: "company", owner_email: null, archived_at: null, created_at: iso(0), level: opts.level,
            page_count: [...pages.values()].filter(visible).length },
          { id: "ks-ops", slug: "team-ops", name: "Operations", purpose: "How the floor runs.", icon: "", kind: "team",
            owner_email: null, archived_at: null, created_at: iso(0), level: "read", page_count: 0 },
        ],
      }));
    }
    if (path === "pages" && method === "GET") {
      const space = url.searchParams.get("space");
      return route.fulfill(json(200, { pages: space === SPACE_ID ? [...pages.values()].filter(visible).map(summary) : [] }));
    }
    if (path === "recent") {
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const mine = url.searchParams.get("mine") === "true";
      const start = Number(url.searchParams.get("cursor") ?? 0);
      const rows = [...pages.values()]
        .filter(visible)
        .filter((p) => !mine || p.author === ME_EMAIL)
        .sort((a, b) => b.updated - a.updated);
      const slice = rows.slice(start, start + limit);
      const next = start + limit < rows.length ? String(start + limit) : null;
      return route.fulfill(json(200, { pages: slice.map(listed), next_cursor: next }));
    }
    if (path === "favorites") {
      const rows = [...stars]
        .sort((a, b) => b.at - a.at)
        .map((s) => pages.get(s.id))
        .filter((p): p is MockPage => Boolean(p && visible(p)));
      return route.fulfill(json(200, {
        pages: rows.map((p) => ({ ...listed(p), starred_at: iso(stars.find((s) => s.id === p.id)?.at ?? 0) })),
        next_cursor: null,
      }));
    }
    if (path === "search") {
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const hits = [...pages.values()]
        .filter((p) => p.state === "published" && p.title.toLowerCase().includes(q.split(" ")[0]))
        .map((p, i) => ({
          page_id: p.id, space_id: SPACE_ID, space_name: "Company", title: p.title, heading_path: p.title,
          text: `${p.title}: the text of the page.`, score: 1 / (i + 1), updated_at: iso(p.updated),
          href: `/knowledge/pages/${p.id}`, icon: p.icon,
        }));
      return route.fulfill(json(200, { query: q, hits, degraded: true }));
    }
    const m = /^pages\/([^/]+)(?:\/(favorite))?$/.exec(path);
    const found = m ? pages.get(decodeURIComponent(m[1])) : undefined;
    if (!m || !found || !visible(found)) return route.fulfill(json(404, { detail: "Not found" }));
    if (m[2] === "favorite") {
      if (method === "POST") {
        if (!stars.some((s) => s.id === found.id)) stars.push({ id: found.id, at: Date.now() });
        return route.fulfill(json(200, { page_id: found.id, favorite: true }));
      }
      const at = stars.findIndex((s) => s.id === found.id);
      if (at >= 0) stars.splice(at, 1);
      return route.fulfill({ status: 204, body: "" });
    }
    if (method === "GET") {
      return route.fulfill(json(200, {
        ...summary(found),
        body_md: "## Scope\n\nThe text of the page.",
        origin: null,
        level: opts.level,
        can_write: writable,
        space_name: "Company",
        versions: Array.from({ length: found.versions }, (_, i) => ({
          version: found.versions - i, title: found.title, author_email: found.author,
          reason: i === found.versions - 1 ? "create" : "edit", via: "web", created_at: iso(found.updated),
        })),
        links: [],
      }));
    }
    if (method === "PATCH") {
      if (!writable) return route.fulfill(json(403, { detail: "Editing this page needs a write grant on its space." }));
      const keys = Object.keys(body ?? {});
      if ("icon" in body) found.icon = body.icon ? String(body.icon) : null;
      if (typeof body.title === "string") found.title = body.title;
      // The gateway's rule: an icon alone is not a change in substance.
      if (!(keys.length === 1 && keys[0] === "icon")) {
        found.versions += 1;
        found.updated = Date.now();
      }
      return route.fulfill(json(200, summary(found)));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { writes, pages, stars };
}
