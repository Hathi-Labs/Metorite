import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InstallNeedRow } from "./NeedsYouCard";
import { isStarred } from "./presets";
import { launcherGroups } from "./shellNav";
import { NAV_SECTIONS, visibleSections } from "@/lib/nav";
import { JOBS, addInstallJobs, buildItems, heldPanes } from "./registry";
import {
  EMPTY_MANIFEST,
  installFeatureSlugs,
  installJobs,
  installNavPanes,
  installNeeds,
  mergeInstallNav,
  parseManifest,
  safeHref,
} from "./packageManifest";

/**
 * PKG-6 (`package_host.md` §5.2). The package manifest adds to the shell and
 * never changes it. Each case is a way the build breaks.
 */

const entry = (key: string, payload: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  key,
  source: "org",
  install_id: "i-1",
  needs_setup: false,
  ...payload,
  ...extra,
});
const manifest = (contributions: Record<string, unknown[]>) =>
  parseManifest({ version: "v1", contributions });

describe("PKG-6 packageManifest", () => {
  it("flag off: an empty manifest leaves the sections and the jobs as they were", () => {
    const base = visibleSections(NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? "")), true);
    expect(mergeInstallNav(base, installNavPanes(EMPTY_MANIFEST))).toBe(base);
    const items = buildItems(heldPanes(base), false, []);
    expect(addInstallJobs(items, installJobs(EMPTY_MANIFEST))).toBe(items);
    expect(installNeeds(EMPTY_MANIFEST)).toEqual([]);
  });

  it("a body that is no manifest reads as empty and never throws", () => {
    for (const body of [null, 1, "x", [], {}, { contributions: [] }, { contributions: { nav: "x" } }]) {
      expect(installNavPanes(parseManifest(body))).toEqual([]);
    }
  });

  it("an org install shows in the Apps section", () => {
    const m = manifest({ nav: [entry("crm", { href: "/zz-crm", label: "CRM", icon: "Users" })] });
    const base = visibleSections([], false);
    const out = mergeInstallNav(base, installNavPanes(m));
    const apps = out.find((s) => s.id === "apps")!;
    expect(apps.items.map((p) => p.href)).toContain("/zz-crm");
    expect(apps.items.find((p) => p.href === "/zz-crm")).toMatchObject({ label: "CRM", launch: "live", team: "apps" });
  });

  it("a feature-gated entry is hidden without the feature", () => {
    const m = manifest({ nav: [entry("crm", { href: "/zz-crm", label: "CRM", feature: "feature:crm" })] });
    expect(installNavPanes(m, [])).toEqual([]);
    expect(installNavPanes(m, ["feature:crm"]).map((p) => p.href)).toEqual(["/zz-crm"]);
  });

  it("system wins: a system href or a system job id is dropped", () => {
    const sysHref = NAV_SECTIONS[0].items[0].href;
    const m = manifest({
      nav: [entry("a", { href: sysHref, label: "Fake" }), entry("b", { href: `${sysHref}/deep`, label: "Fake2" })],
      jobs: [entry(JOBS[0].id, { href: "/x", label: "Clash" })],
    });
    expect(installNavPanes(m)).toEqual([]);
    const base = visibleSections(NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? "")), true);
    const items = buildItems(heldPanes(base), false, JOBS.map((j) => j.feature ?? ""));
    expect(addInstallJobs(items, installJobs(m))).toBe(items);
  });

  it("a system entry in the manifest is ignored", () => {
    const m = manifest({ nav: [entry("s", { href: "/zzz", label: "Z" }, { source: "system", install_id: null })] });
    expect(installNavPanes(m)).toEqual([]);
  });

  it("a malformed entry is dropped", () => {
    const m = manifest({
      nav: [
        entry("no-label", { href: "/a" }),
        entry("no-href", { label: "A" }),
        entry("bad-source", { href: "/b", label: "B" }, { source: "wat" }),
        entry("bad-id", { href: "/c", label: "C" }, { install_id: 4 }),
        "junk",
        entry("good", { href: "/good", label: "Good" }),
      ],
    });
    expect(installNavPanes(m).map((p) => p.href)).toEqual(["/good"]);
  });

  it("a javascript: or absolute-URL href is dropped", () => {
    for (const href of ["javascript:alert(1)", "https://evil.test/x", "//evil.test/x", "/\\evil.test", "crm", "/a\nb"]) {
      expect(safeHref(href)).toBeNull();
    }
    const m = manifest({
      nav: [entry("a", { href: "javascript:alert(1)", label: "A" })],
      jobs: [entry("j", { href: "https://evil.test", label: "J" })],
    });
    expect(installNavPanes(m)).toEqual([]);
    expect(installJobs(m)).toEqual([]);
  });

  it("an install job joins the Do group", () => {
    const m = manifest({ jobs: [entry("new-lead", { href: "/zz-crm?do=lead", label: "New lead", words: ["deal"] })] });
    const items = addInstallJobs(buildItems([], false, []), installJobs(m));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ key: "do:new-lead", group: "do", label: "New lead", words: ["deal"] });
  });

  it("PKG-8c: a Studio job (the payload claims_of writes) is a Do row that opens the app", () => {
    const id = "3f2b7c1e-0a4d-4e8b-9c6f-1d2e3f4a5b6c";
    const m = manifest({
      jobs: [entry(`${id}:visits`, { label: "Log a visit", href: `/apps/${id}`, feature: "studio", app: "Site visits" })],
    });
    const jobs = installJobs(m, ["studio"]);
    expect(jobs).toEqual([
      { id: `${id}:visits`, label: "Log a visit", words: [], href: `/apps/${id}`, icon: "Zap", app: "Site visits" },
    ]);
    const items = addInstallJobs(buildItems([], false, []), jobs);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ key: `do:${id}:visits`, group: "do", label: "Log a visit", href: `/apps/${id}`, hint: "Start · Site visits" });
    expect(items[0].words).toContain("Site visits");
    // A member without the feature of the app page sees no row.
    expect(installJobs(m, [])).toEqual([]);
  });

  it("PKG-8c: two apps with the same verb give two rows that name their app", () => {
    const m = manifest({
      jobs: [
        entry("a1:v", { label: "Add a record", href: "/apps/a1", app: "Clients" }),
        entry("b2:v", { label: "Add a record", href: "/apps/b2", app: "Orders" }),
        entry("c3:v", { label: "Add a record", href: "/apps/c3" }),
      ],
    });
    const items = addInstallJobs(buildItems([], false, []), installJobs(m));
    expect(items.map((i) => i.hint)).toEqual([
      "Start · Clients",
      "Start · Orders",
      "Start · Installed app",
    ]);
    expect(items[1].words).toContain("Orders");
  });

  it("needs_setup gives one row for each install", () => {
    const m = manifest({
      nav: [entry("crm", { href: "/zz-crm", label: "CRM", name: "CRM", setup_href: "/zz-crm/setup" }, { needs_setup: true })],
      jobs: [entry("j", { href: "/zz-crm", label: "J" }, { needs_setup: true })],
    });
    expect(installNeeds(m)).toEqual([{ installId: "i-1", title: "Finish setting up CRM", href: "/zz-crm/setup" }]);
  });

  // ── PKG-6b: the bell row, the launcher and the report ──────────────────────

  it("the bell row: one row for each install, none for an empty manifest", () => {
    const m = manifest({
      nav: [entry("crm", { href: "/zz-crm", label: "CRM", name: "CRM", setup_href: "/zz-crm/setup" }, { needs_setup: true })],
      jobs: [entry("j", { href: "/zz-crm", label: "J" }, { needs_setup: true })],
    });
    const needs = installNeeds(m);
    expect(needs).toHaveLength(1);
    const html = renderToStaticMarkup(createElement(InstallNeedRow, { need: needs[0] }));
    expect(html).toContain("Finish setting up CRM");
    expect(html).toContain('href="/zz-crm/setup"');
    expect(installNeeds(EMPTY_MANIFEST)).toHaveLength(0);
  });

  it("an install with no safe setup_href gives no row", () => {
    const m = manifest({
      nav: [
        entry("a", { href: "/zz-a", label: "A", name: "A" }, { needs_setup: true }),
        entry("b", { href: "/zz-b", label: "B", name: "B", setup_href: "https://evil.example/x" }, { needs_setup: true }),
      ],
    });
    expect(installNeeds(m)).toEqual([]);
  });

  it("a later entry of the same install may give the setup_href", () => {
    const m = manifest({
      nav: [entry("a", { href: "/zz-a", label: "A", name: "A" }, { needs_setup: true })],
      jobs: [entry("j", { href: "/zz-a", label: "J", setup_href: "/zz-a/setup" }, { needs_setup: true })],
    });
    expect(installNeeds(m).map((n) => n.href)).toEqual(["/zz-a/setup"]);
  });

  it("an install pane has an empty note by default and a payload note when given", () => {
    const m = manifest({
      nav: [
        entry("a", { href: "/zz-a", label: "Field visits" }),
        entry("b", { href: "/zz-b", label: "B", description: "Plan the visits" }),
      ],
    });
    const panes = installNavPanes(m);
    expect(panes.map((p) => p.note)).toEqual(["", "Plan the visits"]);
    expect(panes.every((p) => p.team === "apps")).toBe(true);
  });

  it("the bell counts the install rows and hands them to the panel", () => {
    const src = readFileSync(new URL("./ShellBell.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(/count=\{bellCount\(needs\.total, setup\)\}/);
    expect(src).toMatch(/<BellPanel [^>]*extra=\{extra\}/);
    // The My Day card passes no `extra`, so it is unchanged.
    const card = readFileSync(new URL("./NeedsYouCard.tsx", import.meta.url), "utf-8");
    expect(card).toMatch(/<NeedsList needs=\{needs\} now=\{now\}( first=\{first\})? \/>/);
  });

  it("the bell host reads the manifest again when the panel opens", () => {
    const src = readFileSync(new URL("./ShellBell.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(/const refreshManifest = manifest\.refresh;/);
    expect(src).toMatch(/refresh\(\);\s*(\/\/[^\n]*\n\s*)*refreshManifest\(\);/);
  });

  it("All apps lists an install pane, and the stars ignore it", () => {
    const base = visibleSections(NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? "")), true);
    const m = manifest({ nav: [entry("crm", { href: "/zz-crm", label: "CRM" })] });
    const merged = mergeInstallNav(base, installNavPanes(m));
    const listed = launcherGroups(merged).flatMap((g) => g.items.map((p) => p.href));
    expect(listed).toContain("/zz-crm");
    // The stars read the held sections, so a save never names an install href.
    expect(isStarred({ pins: [], known: [] }, base, "/zz-crm")).toBe(false);
    const src = readFileSync(new URL("./AppLauncher.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(/toggleStar\(base, held, href\)/);
    expect(src).toMatch(/isStarred\(shell\.layout, held, p\.href\)/);
    // Empty manifest: the same array, so All apps is as it was.
    expect(launcherGroups(mergeInstallNav(base, installNavPanes(EMPTY_MANIFEST)))).toEqual(launcherGroups(base));
  });

  it("installFeatureSlugs reads the slugs that installs declare", () => {
    const m = manifest({
      nav: [entry("crm", { href: "/zz-crm", label: "CRM", feature: "zz_crm" })],
      jobs: [entry("j", { href: "/zz-crm", label: "J", feature: "zz_jobs" }, { source: "system", install_id: null })],
    });
    expect(installFeatureSlugs(m)).toEqual(["zz_crm"]);
    expect(installFeatureSlugs(EMPTY_MANIFEST)).toEqual([]);
  });
});
