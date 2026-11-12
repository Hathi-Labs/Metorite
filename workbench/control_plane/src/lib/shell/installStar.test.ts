import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NAV_SECTIONS, visibleSections, type NavPane } from "@/lib/nav";
import { clearAll } from "@/lib/dataCache";
import {
  EMPTY_MANIFEST,
  installNavPanes,
  mergeInstallNav,
  mergeStarredInstallNav,
  parseManifest,
} from "./packageManifest";
import { EMPTY_SHELL, canStar, isStarred, starBase, toggleStar } from "./presets";
import { shellSidebar } from "./shellNav";
import { KNOWN_MAX, bodyFor, saveShellPrefs } from "./shellPrefs";

/**
 * PKG-6c-2 (`package_host.md` §9.1, PKG-Q21). An install pane has a star and a
 * hide, and the rail and the drawer keep it only while the stars keep it.
 */

const allFeatures = NAV_SECTIONS.flatMap((s) => s.items.map((p) => p.feature ?? ""));
const held = () => visibleSections(allFeatures, true);
const hostHref = () => held().find((s) => s.id !== "apps" && s.items.length > 1)!.items[0].href;
const entry = (key: string, payload: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  key,
  source: "org",
  install_id: "i-1",
  needs_setup: false,
  ...payload,
  ...extra,
});
const manifest = (nav: unknown[]) => parseManifest({ version: "v1", contributions: { nav } });
const plain = () => manifest([entry("crm", { href: "/zz-crm", label: "CRM" })]);
const withSub = () =>
  manifest([
    entry(hostHref(), { package: "zz-host" }, { source: "system", install_id: null }),
    entry("sub", { href: "/zz-sub", label: "Sub", extends: "zz-host" }),
  ]);
const stars = (pins: string[], known: string[]) => ({ pins, known });
const rail = (m: ReturnType<typeof manifest>, s: ReturnType<typeof stars> | null) => {
  const h = held();
  return mergeStarredInstallNav(h, shellSidebar(h, s), installNavPanes(m), s);
};
const hrefs = (secs: readonly { items: readonly NavPane[] }[]) => secs.flatMap((x) => x.items.map((p) => p.href));
const systemHrefs = () => hrefs(held()).filter((h) => canStar({ href: h } as NavPane));

afterEach(() => {
  clearAll();
  vi.unstubAllGlobals();
});

describe("PKG-6c-2 install star", () => {
  it("(a) an install pane can be starred, and All apps asks canStar and not STARRABLE", () => {
    const [pane] = installNavPanes(plain());
    expect(canStar(pane)).toBe(true);
    const src = readFileSync(new URL("./AppLauncher.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/canStar\(p\)/);
    expect(src).not.toMatch(/STARRABLE\.has/);
  });

  it("(b) hide works on an install row: the toggle over the merged sections writes the install href", () => {
    const merged = mergeInstallNav(held(), installNavPanes(plain()));
    const before = stars([...systemHrefs(), "/zz-crm"], [...systemHrefs(), "/zz-crm"]);
    expect(isStarred(before, merged, "/zz-crm")).toBe(true);
    const after = toggleStar(before, merged, "/zz-crm");
    expect(after.pins).not.toContain("/zz-crm");
    expect(after.known).toContain("/zz-crm");
    expect(isStarred(after, merged, "/zz-crm")).toBe(false);
    // A member with null pins toggles from what the screen shows (starBase).
    const base = starBase({ ...EMPTY_SHELL }, { pins: ["/zz-crm"], known: ["/zz-crm"] });
    expect(toggleStar(base, merged, "/zz-crm").pins).not.toContain("/zz-crm");
  });

  it("(c) the rail drops an unstarred install and keeps a starred one", () => {
    const off = stars(systemHrefs(), [...systemHrefs(), "/zz-crm"]);
    expect(hrefs(rail(plain(), off))).not.toContain("/zz-crm");
    const on = stars([...systemHrefs(), "/zz-crm"], [...systemHrefs(), "/zz-crm"]);
    expect(hrefs(rail(plain(), on))).toContain("/zz-crm");
  });

  it("(c) a starred sub-entry keeps its place after its host, and an unstarred one leaves", () => {
    const known = [...systemHrefs(), "/zz-sub"];
    const on = rail(withSub(), stars([...systemHrefs(), "/zz-sub"], known));
    const sec = on.find((s) => s.items.some((p) => p.href === hostHref()))!;
    const at = sec.items.findIndex((p) => p.href === hostHref());
    expect(sec.items[at + 1].href).toBe("/zz-sub");
    expect(hrefs(rail(withSub(), stars(systemHrefs(), known)))).not.toContain("/zz-sub");
  });

  it("(c) the sub-entry is starrable on its own (PKG-Q21)", () => {
    const [pane] = installNavPanes(withSub());
    expect(pane.parent).toBe(hostHref());
    expect(canStar(pane)).toBe(true);
  });

  it("(d) an install the member never saw shows (rule 4)", () => {
    const s = stars(systemHrefs(), systemHrefs());
    expect(hrefs(rail(plain(), s))).toContain("/zz-crm");
  });

  it("(d) null pins show every install", () => {
    expect(hrefs(rail(plain(), { pins: null, known: null }))).toContain("/zz-crm");
    expect(hrefs(rail(plain(), null))).toContain("/zz-crm");
  });

  it("(e) flag off: the SAME array comes back", () => {
    const h = held();
    const base = shellSidebar(h, stars([], systemHrefs()));
    expect(mergeStarredInstallNav(h, base, installNavPanes(EMPTY_MANIFEST), stars([], []))).toBe(base);
    expect(mergeInstallNav(h, installNavPanes(EMPTY_MANIFEST))).toBe(h);
  });

  it("(f) known is capped at 64 in a save body", () => {
    const many = Array.from({ length: 90 }, (_, i) => `/zz-${i}`);
    expect(KNOWN_MAX).toBe(64);
    expect(bodyFor({ ...EMPTY_SHELL, pins: [], known: many })!.known).toHaveLength(64);
    expect(bodyFor({ ...EMPTY_SHELL, pins: [], known: ["/a"] })!.known).toEqual(["/a"]);
    expect(bodyFor({ ...EMPTY_SHELL, known: null })!.known).toBeNull();
  });

  it("(f) the save answer wins: a dropped install href is not starred", async () => {
    const merged = mergeInstallNav(held(), installNavPanes(plain()));
    const next = { ...EMPTY_SHELL, answered: "answered" as const, ...stars([...systemHrefs(), "/zz-crm"], [...systemHrefs(), "/zz-crm"]) };
    const answer = { ...next, pins: systemHrefs(), known: [...systemHrefs(), "/zz-crm"] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 })));
    const saved = await saveShellPrefs(next);
    expect(saved.pins).not.toContain("/zz-crm");
    expect(isStarred(saved, merged, "/zz-crm")).toBe(false);
  });
});
