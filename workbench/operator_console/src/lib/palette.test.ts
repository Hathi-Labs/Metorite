// The jump palette — and the rule that keeps it honest: pages are DERIVED
// from the sidebar's NAV, so the palette cannot offer a page that no longer
// exists, and cannot miss one that does.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { NAV } from "../app/Header";
import { filterJump, orgItems, pageItems, type JumpItem } from "./palette";

const PAGES = pageItems(NAV);

describe("the page list", () => {
  it("🔴 covers every sidebar destination, tabs included", () => {
    const hrefs = new Set(PAGES.map((p) => p.href));
    for (const group of NAV) {
      for (const item of group.items) {
        expect(hrefs.has(item.href)).toBe(true);
        for (const covered of item.covers ?? []) {
          expect(hrefs.has(covered)).toBe(true);
        }
      }
    }
    // The tab that has no sidebar entry of its own still jumps, by name.
    expect(PAGES.find((p) => p.href === "/providers")?.label).toBe("Providers");
  });

  it("keeps the sidebar's own labels", () => {
    expect(PAGES.find((p) => p.href === "/pricing")?.label).toBe("Pricing");
    expect(PAGES.find((p) => p.href === "/")?.label).toBe("Organizations");
  });
});

describe("the customer rows", () => {
  it("maps name and slug to the customer page", () => {
    expect(orgItems([{ name: "Fracktal Works", slug: "fracktal" }])).toEqual([
      {
        label: "Fracktal Works",
        hint: "fracktal",
        href: "/customers/fracktal",
        keywords: "fracktal works fracktal customer organization",
      },
    ]);
  });

  it("drops garbage rows rather than rendering them", () => {
    expect(orgItems([{ name: 7, slug: "x" }, { name: "ok", slug: "" }])).toEqual([]);
  });

  it("URL-encodes the slug", () => {
    expect(orgItems([{ name: "X", slug: "a b" }])[0].href).toBe(
      "/customers/a%20b",
    );
  });
});

describe("filtering", () => {
  const items: JumpItem[] = [
    ...PAGES,
    ...orgItems([
      { name: "Fracktal Works", slug: "fracktal" },
      { name: "Prime Robotics", slug: "prime" },
    ]),
  ];

  it("an empty query shows the list as given, capped", () => {
    expect(filterJump(items, "", 5)).toEqual(items.slice(0, 5));
  });

  it("🔴 a label prefix outranks a keyword-only hit, whatever the order", () => {
    // Zeta is listed FIRST and matches only through its keywords; Alpha's
    // own name starts with the query. The name the operator typed wins.
    const pair: JumpItem[] = [
      { label: "Zeta", hint: "page", href: "/z", keywords: "alpha" },
      { label: "Alpha", hint: "page", href: "/a", keywords: "alpha" },
    ];
    expect(filterJump(pair, "alp")[0].label).toBe("Alpha");
  });

  it("equal ranks keep the given order — pages before customers", () => {
    // "pri" is a label prefix of Pricing AND of Prime Robotics; the page
    // list is concatenated first, so the page wins the tie.
    const hit = filterJump(items, "pri");
    expect(hit[0].label).toBe("Pricing");
    expect(hit.map((h) => h.label)).toContain("Prime Robotics");
  });

  it("finds a customer by slug through keywords", () => {
    expect(filterJump(items, "fracktal")[0].href).toBe("/customers/fracktal");
  });

  it("answers empty for no match, never a guess", () => {
    expect(filterJump(items, "zzzzz")).toEqual([]);
  });

  it("respects the cap", () => {
    expect(filterJump(items, "", 3)).toHaveLength(3);
  });
});

describe("the wiring", () => {
  const read = (p: string) => readFileSync(join(__dirname, p), "utf8");

  it("🔴 the sidebar mounts it and the shortcut is both Ctrl and Cmd", () => {
    expect(read("../app/Header.tsx")).toContain("<CommandJump />");
    const src = read("../app/CommandJump.tsx");
    expect(src).toContain("ctrlKey");
    expect(src).toContain("metaKey");
    expect(src).toContain('"k"');
  });

  it("fetches customers ONCE, from the roster's own route", () => {
    const src = read("../app/CommandJump.tsx");
    expect(src).toContain('fetch("/api/operator/orgs")');
    // One fetch call in the file — not one per keystroke.
    expect(src.match(/fetch\(/g)).toHaveLength(1);
  });
});
