/**
 * STU-D23a fence: the floor view of the app frame.
 *
 * The frame draws with `renderToStaticMarkup` from a fixed URL. The heavy children are stand-ins
 * that draw one marker, so the test sees which part the frame mounts and which it drops.
 *
 * Mutation 1: draw the rail in the floor view (`rail={railed ? …}` in `AppFrame.tsx`).
 * "drops the rail, the owner verbs, the end-date bar, the setup card and the list bar" turns red.
 * Mutation 2: pass `floor` to `StudioAppFrame` as `true`. "has no data-floor and no signed-in line" turns red.
 * Mutation 3: pass `opened.full` as `full`. "opens a record as the full page, with no way to the
 * side panel" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ query: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {} }),
  usePathname: () => "/studio/package-preview",
  useSearchParams: () => new URLSearchParams(nav.query),
}));
vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { name: "Asha Rao", email: "asha@example.com" } }, status: "authenticated" }),
}));
vi.mock("@/components/AppShell", () => ({ useMobileDrawer: () => ({ open: () => {}, close: () => {} }) }));
vi.mock("@/lib/studio/useRailCounts", () => ({ useRailCounts: () => new Map() }));

/** A stand-in component that draws one marker attribute. */
const stub = vi.hoisted(() => (marker: string) => async () => {
  const react = await import("react");
  return { default: () => react.createElement("i", { [marker]: "" }) };
});
vi.mock("@/components/GenerativeUINode", stub("data-stub-node"));
vi.mock("@/components/studio/PackageLifetimeBar", stub("data-stub-lifetime"));
vi.mock("@/components/studio/StudioFilterBar", stub("data-stub-filter-bar"));
vi.mock("@/components/studio/DeletePackageButton", stub("data-stub-delete-app"));
vi.mock("@/components/studio/StudioRecord", stub("data-stub-record"));
vi.mock("@/components/studio/StudioNewButton", stub("data-stub-new"));
vi.mock("@/components/studio/StudioForm", stub("data-stub-form"));

import AppFrame from "./AppFrame";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import type { PackageDetail } from "@/lib/studio/realDoor";

const ACTIONS = [
  { id: "find", uses: "object:job.list" },
  { id: "peek", uses: "object:job.get" },
  { id: "add", uses: "object:job.create" },
];

const DETAIL: PackageDetail = {
  id: "p1",
  name: "Shop jobs",
  access: "owner",
  actions: ACTIONS,
  objects: [{
    id: "o1", name: "job", label: "Job", rules: [],
    fields: [
      { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
      { key: "status", type: "select", label: "Status", required: false, many: false, options: ["Open", "Done"], to: null, hot: false },
    ],
  }],
  views: [
    { id: "jobs", block: "table", object: "job", title: "Jobs" },
    { id: "log", block: "form", object: "job", title: "Log a job" },
  ],
  pages: [
    { id: "today", title: "Today", cells: [{ view: "jobs" }, { view: "log" }] },
    { id: "done", title: "Done", cells: [{ view: "jobs" }] },
  ],
  nav: [],
};

const DOOR: StudioDataDoor = {
  scope: "p1:live",
  read: async () => [],
  run: async () => ({ ok: true }),
} as unknown as StudioDataDoor;

function draw(query: string, owner = true): string {
  nav.query = query;
  return renderToStaticMarkup(createElement(AppFrame, {
    pkg: "p1",
    detail: owner ? DETAIL : { ...DETAIL, access: "viewer" },
    door: DOOR,
    controls: { owner, actions: owner ? ACTIONS : ACTIONS.slice(0, 2) },
    onDeleted: () => {},
    above: createElement("i", { "data-stub-setup": "" }),
  }));
}

beforeEach(() => { nav.query = ""; });

describe("the usual view", () => {
  it("draws the rail, the owner verbs, the end-date bar, the setup card and the list bar", () => {
    const html = draw("pkg=p1");
    expect(html).toMatch(/aria-label="(Hide|Show) the pages"/);
    expect(html).toContain("Open in Studio IDE");
    expect(html).toContain("data-stub-delete-app");
    expect(html).toContain("data-stub-lifetime");
    expect(html).toContain("data-stub-setup");
    expect(html).toContain("data-stub-filter-bar");
  });

  it("has no data-floor and no signed-in line", () => {
    const html = draw("pkg=p1");
    expect(html).not.toContain("data-floor");
    expect(html).not.toContain("Signed in as");
  });

  it("opens a record in the side panel, with the way to the full page", () => {
    const html = draw("pkg=p1&ro=job&rec=r1");
    expect(html).toContain('data-record-page="panel"');
    expect(html).toContain('aria-label="Open full page"');
  });
});

describe("the floor view (?floor=1)", () => {
  it("drops the rail, the owner verbs, the end-date bar, the setup card and the list bar", () => {
    const html = draw("pkg=p1&floor=1");
    expect(html).not.toMatch(/aria-label="(Hide|Show) the pages"/);
    expect(html).not.toContain('aria-label="Pages"');
    expect(html).not.toContain("Open in Studio IDE");
    expect(html).not.toContain("data-stub-delete-app");
    expect(html).not.toContain("data-stub-lifetime");
    expect(html).not.toContain("data-stub-setup");
    expect(html).not.toContain("data-stub-filter-bar");
  });

  it("keeps the cells of the page and the New button", () => {
    const html = draw("pkg=p1&floor=1");
    expect(html).toContain("data-stub-node");
    expect(html).toContain("data-stub-form");
    expect(html).toContain("data-stub-new");
  });

  it("puts data-floor on the root of the frame, once", () => {
    const html = draw("pkg=p1&floor=1");
    expect(html.match(/data-floor=""/g)).toHaveLength(1);
    expect(html.indexOf('data-floor=""')).toBeLessThan(html.indexOf('data-app-bar='));
  });

  it("names the member who is signed in, and says nothing of a shared device", () => {
    const html = draw("pkg=p1&floor=1");
    expect(html).toContain("Signed in as Asha Rao");
    expect(html.toLowerCase()).not.toContain("shared device");
  });

  it("drops the read-only line of a viewer from the app bar, with the rest of the owner bar", () => {
    expect(draw("pkg=p1", false)).toContain("data-app-bar-actions");
    expect(draw("pkg=p1&floor=1", false)).not.toContain("data-app-bar-actions");
    expect(draw("pkg=p1&floor=1")).not.toContain("data-app-bar-actions");
  });

  it("opens a record as the full page, with no way to the side panel", () => {
    const html = draw("pkg=p1&floor=1&ro=job&rec=r1");
    expect(html).toContain('data-record-page="full"');
    expect(html).not.toContain('data-record-page="panel"');
    expect(html).not.toContain('aria-label="Show in the side panel"');
    expect(html).not.toContain('aria-label="Open full page"');
    expect(html).toContain("data-stub-record");
    expect(html).toContain("Back to Today");
  });

  it("is off for any value but 1", () => {
    for (const q of ["floor=0", "floor=true", "floor=", "floor=11", "f.floor=1"]) {
      expect(draw(`pkg=p1&${q}`), q).not.toContain("data-floor");
    }
  });
});
