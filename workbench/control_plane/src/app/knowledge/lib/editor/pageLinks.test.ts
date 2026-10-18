/**
 * The page link picker (WS-61 KB-2b): `[[` and the link control. A pick
 * writes a Markdown link to `/knowledge/pages/<id>`, and nothing to
 * `kb_links` (that is KB-2h).
 */
import { describe, expect, it } from "vitest";

import type { PageSummary } from "../types";
import {
  addressOf,
  linkedPageId,
  loadPageChoices,
  looksLikeAddress,
  matchPages,
  pageLinkHref,
  type PageChoice,
} from "./pageLinks";

const choice = (id: string, title: string, updatedAt: string | null = null): PageChoice => ({
  id,
  title,
  spaceName: "Company",
  updatedAt,
});

describe("matchPages", () => {
  const pages = [
    choice("a", "Refund policy", "2026-10-01"),
    choice("b", "Holidays", "2026-10-03"),
    choice("c", "Public holidays 2027", "2026-10-02"),
  ];

  it("puts a title that starts with the words before one that holds them", () => {
    expect(matchPages(pages, "holi").map((p) => p.id)).toEqual(["b", "c"]);
  });

  it("lists the pages edited last for no words, and leaves out the open page", () => {
    expect(matchPages(pages, "", { exclude: "b" }).map((p) => p.id)).toEqual(["c", "a"]);
  });

  it("stops at the limit", () => {
    expect(matchPages(pages, "", { limit: 1 })).toHaveLength(1);
  });
});

describe("loadPageChoices", () => {
  it("reads each open space, leaves out an archived page, and skips a space that fails", async () => {
    const summary = (id: string, state: PageSummary["state"]) => ({ id, title: id, state, updated_at: null }) as PageSummary;
    const out = await loadPageChoices(
      [
        { id: "s1", name: "Company", archived_at: null },
        { id: "s2", name: "Gone", archived_at: "2026-01-01" },
        { id: "s3", name: "Broken", archived_at: null },
      ],
      async (spaceId) => {
        if (spaceId === "s3") throw new Error("503");
        if (spaceId === "s2") throw new Error("an archived space is never read");
        return [summary("p1", "published"), summary("p2", "archived"), summary("p3", "draft")];
      },
    );
    expect(out.map((p) => `${p.id}@${p.spaceName}`)).toEqual(["p1@Company", "p3@Company"]);
  });
});

describe("the link a pick writes", () => {
  it("writes the page address, and reads it back", () => {
    expect(pageLinkHref("kp 1")).toBe("/knowledge/pages/kp%201");
    expect(linkedPageId("/knowledge/pages/kp%201")).toBe("kp 1");
    expect(linkedPageId("/knowledge/pages/kp-2?edit=1")).toBe("kp-2");
    expect(linkedPageId("https://example.com")).toBeNull();
    expect(linkedPageId(null)).toBeNull();
  });

  it("tells an address from page words, and gives a bare domain https", () => {
    expect(looksLikeAddress("https://x.example/a")).toBe(true);
    expect(looksLikeAddress("example.com/docs")).toBe(true);
    expect(looksLikeAddress("/knowledge/pages/a")).toBe(true);
    expect(looksLikeAddress("refund policy")).toBe(false);
    expect(addressOf("example.com")).toBe("https://example.com");
    expect(addressOf("mailto:a@b.example")).toBe("mailto:a@b.example");
    expect(addressOf("/knowledge/pages/a")).toBe("/knowledge/pages/a");
  });
});
