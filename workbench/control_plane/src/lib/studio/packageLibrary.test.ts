/**
 * BETA-1 fence (task 1809): the Studio library lists the packages of the member,
 * a card opens the app page, and the app page is titled by its package.
 *
 * Mutation 1: drop `mergeLibrary` from `AppsGallery.tsx`. "lists the packages" turns red.
 * Mutation 2: make the card call anything other than `item.href`. "a card opens the app page" turns red.
 * Mutation 3: put "Package preview" or the Preview badge back on the page. "the app page is titled" turns red.
 * Mutation 4: let `createApp` POST `/api/apps` again. "chat first" turns red.
 * Mutation 5: count a package in no pill. "counts" turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  NO_LIST_LINE, appBrief, changedWords, libraryCounts, mergeLibrary, noListLine, packageCard, packageShows,
} from "./packageLibrary";
import type { PackageSummary } from "./realDoor";

const ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f";
const pkg = (over: Partial<PackageSummary> = {}): PackageSummary => ({
  id: ID, name: "Visits", status: "published", live_version: 2, updated_at: "2026-11-05T10:00:00Z", ...over,
});
const app = (slug: string, over: Record<string, unknown> = {}) => ({
  slug, updated_at: "2026-11-01T10:00:00Z", owner_email: "me@x.in", role: "own", live_version: 1,
  visibility: "private", is_template: false, ...over,
});

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const gallery = read("../../app/studio/components/AppsGallery.tsx");
const page = read("../../app/studio/package-preview/page.tsx");

describe("the package card", () => {
  it("shows the name, the state and the last change", () => {
    const live = packageCard(pkg());
    expect(live).toMatchObject({ name: "Visits", state: "live", changedAt: "2026-11-05T10:00:00Z" });
    expect(packageCard(pkg({ live_version: null, status: "draft" })).state).toBe("draft");
    expect(packageCard(pkg({ name: "  " })).name).toBe("Untitled app");
  });

  it("a card opens the app page of its package", () => {
    const c = packageCard(pkg());
    expect(c.href).toBe(`/studio/package-preview?pkg=${ID}`);
    // The gallery draws the card as a button that pushes that href, and nothing else.
    expect(gallery).toContain("onOpen={() => router.push(item.href)}");
    expect(gallery).toMatch(/item\.kind === "package"/);
  });
});

describe("the merged list", () => {
  it("lists the packages beside the Workshop apps, the newest change first", () => {
    const items = mergeLibrary([app("a")], [pkg()]);
    expect(items.map((i) => i.key)).toEqual([`pkg:${ID}`, "app:a"]);
    const older = mergeLibrary([app("a", { updated_at: "2026-11-06T10:00:00Z" })], [pkg()]);
    expect(older.map((i) => i.kind)).toEqual(["workshop", "package"]);
  });

  it("keeps a package that has no change time, last", () => {
    const items = mergeLibrary([app("a")], [pkg({ updated_at: null })]);
    expect(items.map((i) => i.kind)).toEqual(["workshop", "package"]);
  });

  it("the gallery reads the merge, so a package is listed", () => {
    expect(gallery).toContain("mergeLibrary(");
    expect(gallery).toContain("listPackages()");
  });
});

describe("the counts", () => {
  it("count a package in All, in Mine and, with no live version, in Drafts", () => {
    const c = libraryCounts([app("a", { live_version: null })], [pkg(), pkg({ id: "b", live_version: null })], "me@x.in");
    expect(c).toEqual({ all: 3, mine: 3, team: 0, drafts: 2, templates: 0 });
  });

  it("count no package as Team or Template, and the grid shows none there", () => {
    expect(packageShows("team", { live_version: 1 })).toBe(false);
    expect(packageShows("templates", { live_version: 1 })).toBe(false);
    expect(packageShows("drafts", { live_version: 1 })).toBe(false);
    expect(packageShows("drafts", { live_version: null })).toBe(true);
    expect(packageShows("mine", { live_version: 1 })).toBe(true);
  });

  it("agree with the grid for each pill", () => {
    const pkgs = [pkg(), pkg({ id: "b", live_version: null })];
    for (const f of ["all", "mine", "team", "drafts", "templates"] as const) {
      const shown = pkgs.filter((p) => packageShows(f, p)).length;
      const counted = libraryCounts([], pkgs, "me@x.in")[f];
      expect(shown).toBe(counted);
    }
  });
});

describe("the words", () => {
  const now = Date.parse("2026-11-06T12:00:00Z");
  it("says when the package last changed", () => {
    expect(changedWords("2026-11-06T11:55:00Z", now)).toBe("5m ago");
    expect(changedWords("2026-11-06T09:00:00Z", now)).toBe("3h ago");
    expect(changedWords("2026-11-04T12:00:00Z", now)).toBe("2d ago");
    expect(changedWords(null, now)).toBe("No change yet");
  });

  it("the no-list line says what is missing and the next step", () => {
    expect(noListLine("Visit")).toContain("no list action");
    expect(noListLine("Visit")).toContain("Ask Mira to add a list action");
    expect(NO_LIST_LINE).toContain("Ask Mira to add a list action");
  });
});

describe("chat first", () => {
  it("builds a brief for the chat, with or without words", () => {
    expect(appBrief("track loans")).toBe("Build me an app: track loans");
    expect(appBrief("  ")).toContain("Ask me what it should track");
  });

  it("opens the one assistant with the brief, and makes no Workshop app", () => {
    expect(gallery).toContain("openAssistant({ draft: brief, fresh: true })");
    expect(gallery).toContain("/chat?q=");
    expect(gallery).not.toMatch(/fetch\("\/api\/apps",\s*\{\s*method: "POST"/);
    expect(gallery).not.toContain("?seed=");
  });
});

describe("the app page", () => {
  it("is titled by the package, with no Package preview title and no Preview badge", () => {
    expect(page).not.toContain("Package preview");
    expect(page).not.toMatch(/<Badge[^>]*>Preview<\/Badge>/);
    expect(page).toContain("title={detail.data.name}");
  });

  it("shows the no-list line when the object has no list action, and reads no rows", () => {
    expect(page).toContain("noListLine(");
    expect(page).toMatch(/door && object && canList \? sourceKey/);
  });

  it("holds the Delete app action in the bar", () => {
    expect(page).toContain("<DeletePackageButton");
  });
});
