/**
 * PKG-8a fence: the sidebar entry of a published Studio app shows, and opens
 * the app page.
 *
 * Mutation 1: set `INSTALLED_APP_PREFIX` to "/studio/apps/". "keeps the entry
 * of an installed app" turns red, because `systemOwns` drops it.
 * Mutation 2: remove the `/apps` gate from `studio.json`. "is gated by the
 * feature of /studio" turns red.
 * Mutation 3: return `previewHref(id)` for any id. "sends a bad id nowhere"
 * turns red.
 * Mutation 4 (fix round 1): drop `useLiveRefresh` from `usePackageManifest`.
 * "reads again after a Studio write of the assistant" turns red.
 * Mutation 5 (fix round 1): give the phone drawer its own sections again.
 * "the phone drawer and the sidebar merge the installs through one hook"
 * turns red.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { ASSISTANT_WROTE_EVENT } from "@/lib/assistantWrites";
import { peek, read } from "@/lib/dataCache";
import { wireLiveRefresh } from "@/lib/liveRefresh";
import { featureForPath } from "@/lib/access";
import { NAV_SECTIONS, PANES } from "@/lib/nav";
import { installNavPanes, mergeInstallNav, parseManifest } from "@/lib/shell/packageManifest";
import { previewHref } from "@/lib/studio/packagePreview";

import { INSTALLED_APP_PREFIX, installedAppTarget } from "./installedApp";
import { manifestKey, refreshManifest } from "./manifestCache";

const ID = "3f2b7c1e-0a4d-4e8b-9c6f-1d2e3f4a5b6c";
const HREF = `${INSTALLED_APP_PREFIX}${ID}`;
const page = readFileSync(new URL("../../app/apps/[id]/page.tsx", import.meta.url), "utf8");

/** The body of `GET /shell/manifest` after a publish, as the gateway sends it. */
function published(href: string) {
  return parseManifest({
    version: "v1",
    contributions: {
      nav: [
        { key: "/projects", source: "system", install_id: null, needs_setup: false, feature: "projects", package: "projects" },
        { key: href, source: "private", install_id: "i-1", needs_setup: false,
          href, label: "Field visits", icon: "Boxes", feature: "studio" },
      ],
    },
  });
}

describe("the sidebar entry of an installed Studio app", () => {
  it("keeps the entry of an installed app in the Apps section", () => {
    const panes = installNavPanes(published(HREF), ["studio"]);
    expect(panes).toEqual([
      expect.objectContaining({ href: HREF, label: "Field visits", icon: "Boxes", feature: "studio" }),
    ]);
    const apps = mergeInstallNav(NAV_SECTIONS, panes).find((s) => s.id === "apps");
    expect(apps?.items.map((p) => p.href)).toContain(HREF);
  });

  it("drops the same entry under /studio, the trap that the prefix avoids", () => {
    expect(installNavPanes(published(`/studio/apps/${ID}`), ["studio"])).toEqual([]);
  });

  it("hides the entry from a member who cannot open the app page", () => {
    expect(installNavPanes(published(HREF), ["projects"])).toEqual([]);
  });

  it("is gated by the feature of /studio", () => {
    expect(featureForPath("/studio")).toBe("studio");
    expect(featureForPath(HREF)).toBe("studio");
  });

  it("is in no nav pane, so the live pane count does not move", () => {
    expect(PANES.some((p) => p.href.startsWith("/apps"))).toBe(false);
  });
});

describe("/apps/[id]", () => {
  it("sends a package id to its app page", () => {
    expect(installedAppTarget(ID)).toBe(previewHref(ID));
    expect(installedAppTarget(ID.toUpperCase())).toBe(previewHref(ID));
  });

  it("sends a bad id nowhere", () => {
    for (const bad of ["", "x", `${ID}/x`, "../studio", `${ID}?pkg=1`]) {
      expect(installedAppTarget(bad)).toBeNull();
    }
  });

  it("is a server redirect that mounts no shell part", () => {
    expect(page).not.toMatch(/^"use client"/m);
    expect(page).toMatch(/redirect\(target\)/);
    expect(page).toMatch(/notFound\(\)/);
    expect(page).not.toMatch(/NotificationBell|<AgentChat|keydown|CommandPalette/);
  });
});


describe("the sidebar reads the manifest again after a write (fix round 1)", () => {
  const seed = () => read(manifestKey(), async () => published(HREF));

  it("refreshManifest drops the cached manifest", async () => {
    await seed();
    expect(peek(manifestKey())).toBeDefined();
    refreshManifest();
    expect(peek(manifestKey())).toBeUndefined();
  });

  it("reads again after a Studio write of the assistant", async () => {
    await seed();
    const listeners = new Map<string, (e?: unknown) => void>();
    const win = {
      addEventListener: (t: string, fn: (e?: unknown) => void) => { listeners.set(t, fn); },
      removeEventListener: () => {},
    };
    const doc = { visibilityState: "visible", addEventListener: () => {}, removeEventListener: () => {} };
    const timers = { set: (fn: () => void) => { fn(); return 0; }, clear: () => {} };
    const stop = wireLiveRefresh(refreshManifest, { app: "studio", win, doc, timers });
    listeners.get(ASSISTANT_WROTE_EVENT)?.({ detail: { apps: ["studio"] } });
    expect(peek(manifestKey())).toBeUndefined();
    stop();
    const hook = readFileSync(new URL("./packageManifest.ts", import.meta.url), "utf8");
    expect(hook).toMatch(/useLiveRefresh\(refreshManifest, "studio"\)/);
  });

  it("the phone drawer and the sidebar merge the installs through one hook", () => {
    const shell = readFileSync(new URL("../../components/AppShell.tsx", import.meta.url), "utf8");
    const sidebar = readFileSync(new URL("../../components/Sidebar.tsx", import.meta.url), "utf8");
    expect(shell).toMatch(/const drawerSections = useInstallNav\(/);
    expect(sidebar).toMatch(/const railSections = useInstallNav\(/);
    expect(shell).not.toMatch(/mergeInstallNav|installNavPanes/);
    expect(sidebar).not.toMatch(/mergeInstallNav|installNavPanes/);
  });
});
