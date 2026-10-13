// CRM-C1: settings in sections, one section on each page (spec
// crm_platform.md §20.4). The runner has no DOM, so each component is
// rendered to a string or called for its element tree, and a wiring rule is
// a scan of the source.
import { readFileSync } from "node:fs";

import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import RailRow from "@/components/ui/RailRow";

import { boundaryKey } from "../lib/boundary";
import type { CrmMode } from "../lib/connect";
import { pushView } from "../lib/history";
import {
  SETTINGS_SECTIONS,
  defaultSection,
  isSettingsSection,
  sectionLabel,
  sectionRedirect,
  shownSection,
  visibleSections,
  type SettingsSectionId,
} from "../lib/settingsSections";
import { SETUP_HREF } from "../lib/setup";
import type { LostReason, Status } from "../lib/types";
import {
  DEFAULT_VIEW,
  parseView,
  selectSection,
  selectTab,
  serializeView,
  viewHref,
} from "../lib/urlState";
import CrmRail, { SettingsSectionRows } from "./CrmRail";
import PipelineSettings from "./PipelineSettings";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const ids = (list: { id: string }[]) => list.map((s) => s.id);
const MODES: CrmMode[] = ["mirror", "native", null];

const STATUS: Status = {
  id: "Qualify",
  name: "Qualify",
  color: "gray",
  position: 0,
  type: "open",
  is_default: false,
  probability: 10,
};
const LEAD: Status = { ...STATUS, id: "New", name: "New", probability: null };
const REASON: LostReason = { id: "r1", label: "Price", position: 0 };

/** The page's cards, for one caller, one mode and one section (or none). */
function settings(isAdmin: boolean, mode: CrmMode, section?: SettingsSectionId) {
  return renderToStaticMarkup(
    createElement(PipelineSettings, {
      section,
      // The page passes these two for an admin only. A marked card stands in.
      setup: isAdmin ? createElement("section", { "aria-label": "Setup hub" }, "hub") : undefined,
      access: isAdmin ? createElement("section", { "aria-label": "Access card" }, "access") : undefined,
      showAdminCards: isAdmin,
      readOnly: mode === "mirror",
      dealStatuses: [STATUS],
      leadStatuses: [LEAD],
      lostReasons: [REASON],
      saving: false,
      onSaveStatus: async () => true,
      onRemoveStatus: async () => true,
      onReorderStatuses: async () => undefined,
      onSaveLostReason: async () => true,
      onRemoveLostReason: async () => true,
      onReorderLostReasons: async () => undefined,
    }),
  );
}

/** The cards inside the page's one column. */
const inner = (html: string) => html.replace(/^<div[^>]*>/, "").replace(/<\/div>$/, "");

// ── Done when 1: the sections each caller sees, in each mode ───────────────

describe("the sections a caller sees", () => {
  it("shows an admin every section of the mode", () => {
    expect(ids(visibleSections({ isAdmin: true, mode: "mirror" }))).toEqual([
      "setup",
      "access",
      "stages",
      "lead-statuses",
      "lost-reasons",
      "fields",
      "field-mapping",
      "sync-health",
    ]);
    for (const mode of ["native", null] as CrmMode[]) {
      expect(ids(visibleSections({ isAdmin: true, mode }))).toEqual([
        "setup",
        "access",
        "stages",
        "lead-statuses",
        "lost-reasons",
        "fields",
      ]);
    }
  });

  it("shows a member the three lists in each mode", () => {
    for (const mode of MODES) {
      expect(ids(visibleSections({ isAdmin: false, mode }))).toEqual([
        "stages",
        "lead-statuses",
        "lost-reasons",
      ]);
    }
  });

  it("draws at least one card in each visible section", () => {
    for (const isAdmin of [true, false]) {
      for (const mode of MODES) {
        for (const section of visibleSections({ isAdmin, mode })) {
          const html = settings(isAdmin, mode, section.id as SettingsSectionId);
          expect(html, `${section.id} ${isAdmin} ${mode}`).toMatch(/<section/);
        }
      }
    }
  });

  it("holds one list, with a label and an icon on each row", () => {
    expect(new Set(ids([...SETTINGS_SECTIONS])).size).toBe(SETTINGS_SECTIONS.length);
    for (const section of SETTINGS_SECTIONS) {
      expect(section.label).not.toBe("");
      expect(section.icon).toMatch(/^[A-Z]/);
    }
    expect(sectionLabel("stages")).toBe("Deal stages");
  });
});

// ── Done when 2: a member in Native or null mode ──────────────────────────

describe("a member in Native or null mode", () => {
  it("opens the deal grid, the lead grid and the reason grid, one at a time", () => {
    for (const mode of ["native", null] as CrmMode[]) {
      const stages = settings(false, mode, "stages");
      expect(stages).toContain("Pipelines and stages");
      expect(stages).not.toContain("Lead Status");
      expect(stages).not.toContain("Lost reasons");
      const leads = settings(false, mode, "lead-statuses");
      expect(leads).toContain("Lead Status");
      expect(leads).not.toContain("Pipelines and stages");
      const lost = settings(false, mode, "lost-reasons");
      expect(lost).toContain("Lost reasons");
      expect(lost).not.toContain("Lead Status");
    }
  });

  it("does not draw an admin card, whatever the section", () => {
    for (const section of ["setup", "access", "fields", "field-mapping", "sync-health"] as const) {
      expect(inner(settings(false, "native", section)), section).toBe("");
    }
  });
});

// ── Done when 3: a hidden section goes to the default through replaceView ──

describe("a missing, unknown or hidden section", () => {
  it("resolves to the default of the caller", () => {
    const native = { isAdmin: true, mode: "native" as CrmMode };
    expect(sectionRedirect({ tab: "settings", section: "sync-health" }, native)).toBe("setup");
    expect(sectionRedirect({ tab: "settings", section: "sync-health" }, { isAdmin: false, mode: "native" })).toBe(
      "stages",
    );
    expect(sectionRedirect({ tab: "settings", section: null }, native)).toBe("setup");
    expect(sectionRedirect({ tab: "settings", section: "access" }, { isAdmin: false, mode: null })).toBe("stages");
    expect(sectionRedirect({ tab: "settings", section: "fields" }, native)).toBeNull();
    expect(sectionRedirect({ tab: "deals", section: null }, native)).toBeNull();
    expect(shownSection("sync-health", native)).toBe("setup");
    expect(defaultSection({ isAdmin: false, mode: "mirror" })).toBe("stages");
    // An unknown value parses as null, so it takes the same path.
    expect(parseView("tab=settings&section=nope").section).toBeNull();
  });

  it("is sent there by one effect in the page, through replaceView", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(
      /const redirect = sectionRedirect\(view, sectionCtx\);\s*if \(redirect\) replaceView\(viewHref\(\{ \.\.\.view, section: redirect \}\)\);/,
    );
    // `lib/history.test.ts` refuses router.push and router.replace in each CRM file.
  });
});

// ── Done when 4: a child row pushes the section, and a phone closes the drawer ─

/** The elements of a node, flat. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

describe("a click on a child row", () => {
  it("hands the section to the page", () => {
    const onSelect = vi.fn();
    const rows = elements(
      SettingsSectionRows({ sections: visibleSections({ isAdmin: true, mode: "mirror" }), active: "setup", onSelect }),
    ).filter((el) => el.type === RailRow);
    const lead = rows.find((el) => (el.props as { label: string }).label === "Lead statuses");
    (lead!.props as { onSelect: () => void }).onSelect();
    expect(onSelect).toHaveBeenCalledWith("lead-statuses");
  });

  it("pushes the section's address", () => {
    const history = { pushState: vi.fn(), replaceState: vi.fn() };
    pushView(viewHref(selectSection(DEFAULT_VIEW, "lead-statuses")), history);
    expect(history.pushState).toHaveBeenCalledWith(null, "", "/crm?tab=settings&section=lead-statuses");
    expect(history.replaceState).not.toHaveBeenCalled();
  });

  it("goes through the page's pushView, and closes the drawer on a phone", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(
      /function chooseSection\(section: SettingsSectionId\): void \{\s*go\(selectSection\(view, section\)\);\s*if \(isMobile\) closeDrawer\(\);\s*\}/,
    );
    // Both rails: the phone drawer and the desktop column.
    expect(page.match(/settings=\{railSettings\}/g)?.length).toBe(2);
    expect(page).toMatch(/onSelect: chooseSection/);
  });
});

// ── Done when 5: the URL ───────────────────────────────────────────────────

describe("?section=", () => {
  it("parses on the settings tab only", () => {
    expect(parseView("tab=settings&section=stages").section).toBe("stages");
    expect(parseView("tab=deals&section=stages").section).toBeNull();
    expect(parseView("tab=reports&section=fields").section).toBeNull();
    expect(isSettingsSection("fields")).toBe(true);
    expect(isSettingsSection("Fields")).toBe(false);
  });

  it("travels in the address, and selectTab clears it", () => {
    const view = selectSection(DEFAULT_VIEW, "fields");
    expect(serializeView(view)).toBe("tab=settings&section=fields");
    expect(parseView(serializeView(view))).toEqual(view);
    const away = selectTab(view, "deals");
    expect(away.section).toBeNull();
    expect(serializeView(away)).toBe("");
    // A section on another tab is never written.
    expect(serializeView({ ...DEFAULT_VIEW, section: "fields" })).toBe("");
  });

  it("keeps the open record when the section changes", () => {
    const open = { ...selectSection(DEFAULT_VIEW, "stages"), record: { param: "deal" as const, entity: "deals" as const, id: "d1" } };
    expect(selectSection(open, "fields").record).toEqual(open.record);
  });
});

// ── Done when 6: with no section, every card of today ──────────────────────

describe("PipelineSettings with no section", () => {
  it("draws every card, which is each section's cards in rail order", () => {
    for (const isAdmin of [true, false]) {
      for (const mode of MODES) {
        const whole = inner(settings(isAdmin, mode));
        const parts = visibleSections({ isAdmin, mode })
          .map((s) => inner(settings(isAdmin, mode, s.id as SettingsSectionId)))
          .join("");
        expect(parts, `${isAdmin} ${mode}`).toBe(whole);
      }
    }
  });

  it("still draws the cards that the earlier slices fence", () => {
    const admin = settings(true, "mirror");
    for (const text of ["Setup hub", "Access card", "Reset order to Zoho", "Lost reasons", "Sync health"]) {
      expect(admin).toContain(text);
    }
  });
});

// ── Done when 7: the error boundary ────────────────────────────────────────

describe("boundaryKey", () => {
  it("changes when only the section changes", () => {
    const a = boundaryKey({ tab: "settings", layout: "list", section: "stages" }, null);
    const b = boundaryKey({ tab: "settings", layout: "list", section: "fields" }, null);
    expect(a).not.toBe(b);
    expect(boundaryKey({ tab: "deals", layout: "board" }, null)).toBe("deals:board");
  });
});

// ── Done when 8: the child rows in the rail ────────────────────────────────

describe("the rail with settings open", () => {
  const sections = visibleSections({ isAdmin: true, mode: "mirror" });

  it("draws one depth-1 RailRow for each section", () => {
    const rows = elements(SettingsSectionRows({ sections, active: "stages", onSelect: () => undefined }));
    const rails = rows.filter((el) => el.type === RailRow);
    expect(rails.length).toBe(sections.length);
    for (const row of rails) expect((row.props as { depth: number }).depth).toBe(1);
    expect(rails.map((row) => (row.props as { label: string }).label)).toEqual(sections.map((s) => s.label));
  });

  it("nests the rows under CRM settings, and highlights the child, not the parent", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "settings",
        onSelect: () => undefined,
        settings: { sections, active: "stages", onSelect: () => undefined },
      }),
    );
    expect(html.indexOf("CRM settings")).toBeLessThan(html.indexOf("Setup and sync"));
    expect(html.match(/aria-current="page"/g)?.length).toBe(1);
    const current = html.split("<button").find((chunk) => chunk.includes('aria-current="page"'));
    expect(current).toContain("Deal stages");
    expect(current).not.toContain("CRM settings");
  });

  it("draws no child row on another tab", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "deals",
        onSelect: () => undefined,
        settings: { sections, active: null, onSelect: () => undefined },
      }),
    );
    expect(html).not.toContain("Setup and sync");
  });
});

// ── Done when 9 and item 11: the setup link ────────────────────────────────

describe("the setup link", () => {
  it("opens the setup section", () => {
    expect(SETUP_HREF).toBe("/crm?tab=settings&section=setup");
    expect(parseView(SETUP_HREF.split("?")[1]).section).toBe("setup");
  });

  it("is opened by the banner and the title-bar line through pushView", () => {
    const banner = read("./MirrorBanner.tsx");
    expect(banner).toMatch(/pushView\(SETUP_HREF\)/);
    expect(banner).not.toMatch(/from "next\/link"/);
    expect(read("../page.tsx")).toContain('onOpenSetup={() => chooseSection("setup")}');
  });
});

// ── Item 9: the title bar names the section ────────────────────────────────

describe("the title bar", () => {
  it("names the section on screen", () => {
    expect(read("../page.tsx")).toMatch(/`Settings · \$\{sectionLabel\(settingsSection\)\}`/);
  });
});
