import { describe, expect, it } from "vitest";
import { NAV_SECTIONS, visibleSections } from "@/lib/nav";
import { JOBS, addInstallJobs, buildItems, heldPanes } from "./registry";
import {
  EMPTY_MANIFEST,
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
    expect(apps.items.find((p) => p.href === "/zz-crm")).toMatchObject({ label: "CRM", launch: "live", team: "across" });
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

  it("needs_setup gives one row for each install", () => {
    const m = manifest({
      nav: [entry("crm", { href: "/zz-crm", label: "CRM", name: "CRM", setup_href: "/zz-crm/setup" }, { needs_setup: true })],
      jobs: [entry("j", { href: "/zz-crm", label: "J" }, { needs_setup: true })],
    });
    expect(installNeeds(m)).toEqual([{ installId: "i-1", title: "Finish setting up CRM", href: "/zz-crm/setup" }]);
  });
});
