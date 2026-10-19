import { expect, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The mock of the Knowledge BFF for the page history (WS-61 KB-2g): one page
 * with five versions. `knowledge-history.spec.ts` drives it, and a visual
 * review imports it to capture the same states.
 */

const SPACE_ID = "ks-company";
export const PAGE_ID = "kp-refunds";

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

type Version = { version: number; title: string; body_md: string; reason: string; author: string; at: string };

/** Five versions. Version 2 says "seven days", version 5 says "ten days". */
function fiveVersions(): Version[] {
  const at = (h: number) => `2026-10-1${h}T09:30:00Z`;
  return [
    { version: 1, title: "Refunds", body_md: "Draft.", reason: "create", author: "asha@example.com", at: at(1) },
    {
      version: 2,
      title: "Refund policy",
      body_md: "# Refunds\n\nRefunds take seven days.\n\nAsk the front desk.",
      reason: "edit",
      author: "ravi.kumar@example.com",
      at: at(2),
    },
    {
      version: 3,
      title: "Refund policy",
      body_md: "# Refunds\n\nRefunds take eight days.\n\nAsk the front desk.",
      reason: "edit",
      author: "asha@example.com",
      at: at(3),
    },
    {
      version: 4,
      title: "Refund policy",
      body_md: "# Refunds\n\nRefunds take eight days.\n\nAsk the front desk.",
      reason: "publish",
      author: "asha@example.com",
      at: at(4),
    },
    {
      version: 5,
      title: "Refund policy",
      body_md: "# Refunds\n\nRefunds take ten days.\n\nAsk the front desk.\n\nKeep the receipt.",
      reason: "edit",
      author: "meera@example.com",
      at: at(5),
    },
  ];
}

/** A stateful mock of the Knowledge BFF for one page. Returns the log of each write. */
export async function mockHistory(
  page: Page,
  opts: { level?: "read" | "write"; missing?: number[] } = {},
) {
  const level = opts.level ?? "write";
  const versions = fiveVersions();
  const writes: Array<{ method: string; path: string; body: unknown }> = [];
  const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  const latest = () => versions[versions.length - 1];
  const detail = () => ({
    id: PAGE_ID,
    space_id: SPACE_ID,
    parent_id: null,
    position: 0,
    title: latest().title,
    state: "published",
    author_email: "asha@example.com",
    reviewers: [],
    review_at: "2027-04-14T00:00:00Z",
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: latest().at,
    created_at: versions[0].at,
    body_md: latest().body_md,
    origin: null,
    level,
    can_write: level === "write",
    versions: [...versions].reverse().map((v) => ({
      version: v.version,
      title: v.title,
      author_email: v.author,
      reason: v.reason,
      via: "web",
      created_at: v.at,
    })),
    links: [],
  });

  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/knowledge\//, "");
    const method = req.method();
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    if (method !== "GET") writes.push({ method, path, body });
    if (path === "spaces") {
      return route.fulfill(json(200, {
        spaces: [{ id: SPACE_ID, slug: "company", name: "Company", purpose: "How we do things.", icon: "",
          kind: "company", owner_email: null, archived_at: null, created_at: versions[0].at, level, page_count: 1 }],
      }));
    }
    if (path === "pages" && method === "GET") {
      const { body_md: _b, origin: _o, level: _l, can_write: _c, versions: _v, links: _k, ...summary } = detail();
      return route.fulfill(json(200, { pages: [summary] }));
    }
    const v = new RegExp(`^pages/${PAGE_ID}/versions/(\\d+)$`).exec(path);
    if (v && method === "GET") {
      const n = Number(v[1]);
      const found = versions.find((x) => x.version === n);
      if (!found || opts.missing?.includes(n)) return route.fulfill(json(404, { detail: "Not found" }));
      return route.fulfill(json(200, {
        page_id: PAGE_ID, version: n, title: found.title, body_md: found.body_md,
        author_email: found.author, reason: found.reason, via: "web", created_at: found.at,
      }));
    }
    if (path === `pages/${PAGE_ID}` && method === "GET") return route.fulfill(json(200, detail()));
    if (path === `pages/${PAGE_ID}/revert` && method === "POST") {
      if (level !== "write") return route.fulfill(json(403, { detail: "Needs a write grant." }));
      const old = versions.find((x) => x.version === body.version);
      if (!old) return route.fulfill(json(404, { detail: "Not found" }));
      versions.push({ ...old, version: versions.length + 1, reason: "revert", author: "asha@example.com",
        at: "2026-10-19T10:00:00Z" });
      return route.fulfill(json(200, { ...detail(), version: versions.length }));
    }
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { writes };
}

export async function setupHistory(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member() });
}

/** Open the page and its history. Returns the dialog. */
export async function openHistory(page: Page) {
  await page.goto(`/knowledge/pages/${PAGE_ID}`);
  await page.getByRole("button", { name: "Page history", exact: true }).click({ timeout: 30_000 });
  const dialog = page.getByRole("dialog", { name: "Page history" });
  await expect(dialog).toBeVisible();
  return dialog;
}
