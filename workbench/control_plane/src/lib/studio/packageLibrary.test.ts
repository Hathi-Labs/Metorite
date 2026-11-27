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
  ACTION_CALLS_LABEL, NO_ERRORS_LINE, NO_LIST_LINE, NO_RUNS_LINE, USE_NOT_TRACKED_LINE, appBrief, callsWords,
  changedWords, effectiveFilter, libraryCounts, membersPill, mergeLibrary, noListLine, orgCard, packageCard, packageShows,
} from "./packageLibrary";
import type { OrgPackage, PackageSummary } from "./realDoor";

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
    // G3a fix 1: "Build it" goes through `startBuild`. The IDE sends the brief after its facts publish.
    // MOB-12: the gallery and the phone bar's New app tab share `startApp`, which holds that flow.
    const start = read("./startApp.ts");
    expect(gallery).toContain("startApp(seed,");
    expect(start).toContain("startBuild(");
    expect(start).toContain("/chat?q=");
    expect(gallery).not.toContain("openAssistant(");
    expect(start).not.toContain("openAssistant(");
    expect(gallery).not.toMatch(/fetch\("\/api\/apps",\s*\{\s*method: "POST"/);
    expect(gallery).not.toContain("?seed=");
  });
});

describe("the gallery loads both reads before it clears the skeleton", () => {
  it("waits for the packages, and drops an older answer", () => {
    expect(gallery).toContain("await packagesRead;");
    expect(gallery.indexOf("await packagesRead;")).toBeLessThan(gallery.indexOf("setLoading(false);\n      // The admin read starts AFTER"));
    expect(gallery).toContain("seq !== packagesSeq.current");
  });
  it("draws the state chip with the shared Badge", () => {
    expect(gallery).toContain('<Badge tone={live ? "success" : "warning"}');
  });
  it("the chat page reads ?q= with the shell bar off, for the fallback link", () => {
    const chat = read("../../app/chat/page.tsx");
    expect(chat).toContain('const askParam = searchParams?.get("q") ?? null;');
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
    expect(page).toMatch(/door && object && canList( && !APP_FRAME)? \? sourceKey/);
  });

  it("holds the Delete app action in the bar", () => {
    expect(page).toContain("<DeletePackageButton");
  });
});

// --- the admin view (STU-J4) ---

const org = (over: Partial<OrgPackage> = {}): OrgPackage => ({
  ...pkg(), owner_email: "ben@x.in", mine: false, action_calls: 0, action_calls_capped: false, last_error: null, ...over,
});

describe("the admin view of packages", () => {
  it("a package with no audit row shows the three honest empty states", () => {
    const c = orgCard(org({ live_version: null, status: "draft" }));
    expect(c).toMatchObject({ state: "draft", version: "No live version", calls: NO_RUNS_LINE, errorLine: NO_ERRORS_LINE, use: USE_NOT_TRACKED_LINE, error: null });
    expect(NO_RUNS_LINE).toBe("No runs yet");
    expect(NO_ERRORS_LINE).toBe("No errors");
    expect(USE_NOT_TRACKED_LINE).toBe("Not tracked yet");
  });

  it("names the owner, the live version, the count and the last error", () => {
    const now = Date.parse("2026-11-11T12:00:00Z");
    const c = orgCard(org({
      owner_email: "ben@x.in", live_version: 3, action_calls: 1204,
      last_error: { action: "close_visit", outcome: "refused", at: "2026-11-11T10:00:00Z" },
    }), now);
    expect(c).toMatchObject({ owner: "ben@x.in", state: "live", version: "v3", calls: "1,204 action calls" });
    expect(c.errorLine).toBe("Refused: close_visit, 2h ago");
    expect(c.error).toEqual({ action: "close_visit", outcome: "refused", when: "2h ago" });
  });

  it("shows no reason text: the card has no field for one", () => {
    const withReason = org({ last_error: { action: "a", outcome: "failed", at: null, reason: "secret detail" } as never });
    expect(JSON.stringify(orgCard(withReason))).not.toContain("secret detail");
  });

  it("counts say the number, the singular and the cap", () => {
    expect(callsWords(0, false)).toBe(NO_RUNS_LINE);
    expect(callsWords(1, false)).toBe("1 action call");
    expect(callsWords(10000, true)).toBe("10,000+ action calls");
    expect(ACTION_CALLS_LABEL).toBe("Action calls");
  });

  it("the pill exists only when the gateway said admin", () => {
    expect(membersPill(false, 5)).toBeNull();
    expect(membersPill(true, 5)).toEqual({ id: "members", label: "All members", count: 5 });
  });

  it("the members filter shows no member package beside the admin grid", () => {
    expect(packageShows("members", { live_version: 1 })).toBe(false);
  });

  it("the gallery reads the admin answer, and guesses nothing from the role", () => {
    expect(gallery).toContain("listOrgPackages()");
    expect(gallery).toContain("membersPill(orgAdmin");
    expect(gallery).not.toMatch(/admin:members|isAdmin|role === "admin"/);
  });

  it("a chosen members filter falls back to all when the pill goes, for the grid and the pills alike", () => {
    expect(effectiveFilter("members", true)).toBe("members");
    expect(effectiveFilter("members", false)).toBe("all");
    expect(effectiveFilter("drafts", false)).toBe("drafts");
    expect(gallery).toContain("const filter = effectiveFilter(chosen, members !== null)");
    expect(gallery).toContain("activeId={filter}");
  });

  it("the org list loads AFTER the library shows, and never holds the skeleton", () => {
    const body = gallery.slice(gallery.indexOf("const fetchApps"));
    const finish = body.indexOf("setLoading(false)");
    expect(finish).toBeGreaterThan(0);
    expect(body.indexOf("void readOrg()")).toBeGreaterThan(finish);
    expect(body.slice(0, finish)).not.toContain("listOrgPackages");
    expect(body.slice(0, finish)).not.toMatch(/await readOrg|await orgRead/);
  });

  it("the admin card gives the full owner address and breaks an action id at underscores", () => {
    expect(gallery).toContain("title={card.owner}");
    expect(gallery).toContain("breakAtUnderscores(card.errorLine)");
  });
});
