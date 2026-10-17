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
import { FIELD_MAP_TITLE } from "../lib/fieldMap";
import { pushView } from "../lib/history";
import { draftOf } from "../lib/settings";
import {
  SETTINGS_SECTIONS,
  defaultSection,
  isSettingsSection,
  sectionLabel,
  sectionRedirect,
  shownSection,
  visibleSections,
  withShownSection,
  type SettingsSectionId,
} from "../lib/settingsSections";
import { useCrmStore } from "../lib/store";
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
function settings(
  isAdmin: boolean,
  mode: CrmMode,
  section?: SettingsSectionId,
  served: { deal?: Status; reason?: LostReason } = {},
  studioAdmin = false,
) {
  return renderToStaticMarkup(
    createElement(PipelineSettings, {
      section,
      // CRM-C6a: `studio_admin` of the layout. The page gives it to the
      // sections and to the cards.
      studioAdmin,
      // The page passes these two for an admin only. A marked card stands in.
      setup: isAdmin ? createElement("section", { "aria-label": "Setup hub" }, "hub") : undefined,
      access: isAdmin ? createElement("section", { "aria-label": "Access card" }, "access") : undefined,
      showAdminCards: isAdmin,
      readOnly: mode === "mirror",
      dealStatuses: [served.deal ?? STATUS],
      leadStatuses: [LEAD],
      lostReasons: [served.reason ?? REASON],
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
      "suggestions",
      "sync-health",
    ]);
    // Native with no studio: the Field mapping section is hidden (CRM-C6a).
    for (const mode of ["native", null] as CrmMode[]) {
      for (const studioAdmin of [undefined, false]) {
        expect(ids(visibleSections({ isAdmin: true, mode, studioAdmin }))).toEqual([
          "setup",
          "access",
          "stages",
          "lead-statuses",
          "lost-reasons",
          "fields",
        ]);
      }
    }
  });

  it("shows Field mapping to a Native admin only when the layout says studio_admin", () => {
    for (const mode of ["native", null] as CrmMode[]) {
      expect(ids(visibleSections({ isAdmin: true, mode, studioAdmin: true }))).toEqual([
        "setup",
        "access",
        "stages",
        "lead-statuses",
        "lost-reasons",
        "fields",
        "field-mapping",
      ]);
      // A member never opens it, whatever the flag says.
      expect(ids(visibleSections({ isAdmin: false, mode, studioAdmin: true }))).not.toContain(
        "field-mapping",
      );
    }
    // Mirror mode needs no studio: its Field map is the CRM-Q7a card.
    expect(ids(visibleSections({ isAdmin: true, mode: "mirror", studioAdmin: false }))).toContain(
      "field-mapping",
    );
  });

  it("draws the conversion mapping in Native mode and the Field map in Mirror mode", () => {
    const native = settings(true, "native", "field-mapping", {}, true);
    expect(native).toContain(">Field mapping</h2>");
    expect(inner(settings(true, "native", "field-mapping"))).toBe("");
    // The Q7a Field map card stays Mirror only (PipelineSettings.tsx:301).
    expect(read("./PipelineSettings.tsx")).toContain(
      '{readOnly && show("field-mapping") && <FieldMapCard />}',
    );
    expect(read("./PipelineSettings.tsx")).toContain(
      '{!readOnly && studioAdmin && show("field-mapping") && <ConvertMappingCard />}',
    );
    expect(read("../page.tsx")).toMatch(/studioAdmin=\{studioAdmin\}/);
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
      expect(stages).toContain(">Deal stages</h2>");
      expect(stages).not.toContain("Lead statuses");
      expect(stages).not.toContain("Lost reasons");
      const leads = settings(false, mode, "lead-statuses");
      expect(leads).toContain(">Lead statuses</h2>");
      expect(leads).not.toContain("Deal stages");
      const lost = settings(false, mode, "lost-reasons");
      expect(lost).toContain(">Lost reasons</h2>");
      expect(lost).not.toContain("Lead statuses");
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
  const native = { isAdmin: true, mode: "native" as CrmMode };

  it("rewrites a missing or unknown section to the default of the caller", () => {
    expect(sectionRedirect({ tab: "settings", section: null }, native, true)).toBe("setup");
    expect(sectionRedirect({ tab: "settings", section: null }, { isAdmin: false, mode: null }, true)).toBe(
      "stages",
    );
    expect(sectionRedirect({ tab: "settings", section: "nope" }, native, true)).toBe("setup");
    expect(sectionRedirect({ tab: "settings", section: "fields" }, native, true)).toBeNull();
    expect(sectionRedirect({ tab: "deals", section: null }, native, true)).toBeNull();
    expect(defaultSection({ isAdmin: false, mode: "mirror" })).toBe("stages");
    // An unknown value parses as null, so it takes the same path.
    expect(parseView("tab=settings&section=nope").section).toBeNull();
  });

  it("draws the default for a known hidden section, and keeps its URL", () => {
    // Done when 3, as the review changed it: `section=sync-health` in Native
    // mode shows the default. The URL stays, because the mode or the caller
    // can be a guess for a moment.
    expect(sectionRedirect({ tab: "settings", section: "sync-health" }, native, true)).toBeNull();
    expect(shownSection("sync-health", native)).toBe("setup");
    const member = { isAdmin: false, mode: "native" as CrmMode };
    expect(sectionRedirect({ tab: "settings", section: "setup" }, member, true)).toBeNull();
    expect(shownSection("setup", member)).toBe("stages");
  });

  it("rewrites nothing while the access or the mode is a guess", () => {
    // A stale access read is NO_ACCESS: the admin reads as a member.
    const guess = { isAdmin: false, mode: null as CrmMode };
    for (const section of [null, "nope", "setup", "sync-health"]) {
      expect(sectionRedirect({ tab: "settings", section }, guess, false), String(section)).toBeNull();
    }
  });

  it("is sent there by one effect in the page, through replaceView, once the reads settle", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(
      /const sectionTrusted = !accessLoading && !accessStale && !ctx\.modeReadFailed;/,
    );
    expect(page).toMatch(
      /const redirect = sectionRedirect\(view, sectionCtx, sectionTrusted\);\s*if \(redirect\) replaceView\(viewHref\(\{ \.\.\.view, section: redirect \}\)\);/,
    );
    expect(page).toContain("const { loading: accessLoading, stale: accessStale } = useAccess();");
    expect(page).toContain("modeReadFailed: readFailed,");
    expect(read("../lib/useCrmMode.ts")).toMatch(/^ {4}readFailed,\r?$/m);
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
      /function chooseSection\(section: SettingsSectionId\): void \{\s*const next = selectSection\(view, section\);\s*if \(next !== view\) go\(next\);\s*if \(isMobile\) closeDrawer\(\);\s*\}/,
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
        for (const studioAdmin of [false, true]) {
          const whole = inner(settings(isAdmin, mode, undefined, {}, studioAdmin));
          const parts = visibleSections({ isAdmin, mode, studioAdmin })
            .map((s) => inner(settings(isAdmin, mode, s.id as SettingsSectionId, {}, studioAdmin)))
            .join("");
          expect(parts, `${isAdmin} ${mode} ${studioAdmin}`).toBe(whole);
        }
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
    expect(banner).toMatch(/onClick=\{\(event\) => pushOnPlainClick\(event, SETUP_HREF\)\}/);
    expect(banner).not.toMatch(/from "next\/link"/);
    expect(read("../page.tsx")).toContain('onOpenSetup={() => chooseSection("setup")}');
  });
});

// ── Review: a click on the view on screen adds no history entry ────────────

describe("a click on the section or the view on screen", () => {
  it("returns the same view, so the page pushes nothing", () => {
    const fields = selectSection(DEFAULT_VIEW, "fields");
    expect(selectSection(fields, "fields")).toBe(fields);
    expect(selectTab(fields, "settings")).toBe(fields);
    expect(selectSection(fields, "access")).not.toBe(fields);
  });

  it("is checked by both rail handlers of the page", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(
      /function chooseView\(tab: CrmView\["tab"\]\): void \{[^}]*const next = selectTab\(view, tab\);\s*if \(next !== view\) go\(next\);/,
    );
  });
});

// ── Review: the boundary key reads the section on screen ───────────────────

describe("the boundary of a settings URL", () => {
  it("keeps one key across the rewrite of a missing section", () => {
    const admin = { isAdmin: true, mode: "native" as CrmMode };
    const key = (search: string) => boundaryKey(withShownSection(parseView(search), admin), null);
    expect(key("tab=settings")).toBe(key("tab=settings&section=setup"));
    expect(key("tab=settings&section=sync-health")).toBe(key("tab=settings&section=setup"));
    expect(key("tab=settings&section=fields")).not.toBe(key("tab=settings&section=setup"));
    expect(key("tab=deals")).toBe("deals:board");
    expect(read("../page.tsx")).toContain(
      "<CrmBoundary key={boundaryKey(withShownSection(view, sectionCtx), pipelineId)}>",
    );
  });
});

// ── Review: an unsaved edit survives a section change ──────────────────────

describe("a draft across a section change", () => {
  const typed = (html: string, text: string) => html.includes(`value="${text}"`);
  // A server render reads the store's INITIAL state (zustand's server
  // snapshot), and this runner has no DOM. So the test copies the live drafts
  // there before each render. The browser reads the live state.
  const initial = useCrmStore.getInitialState();
  const settingsNow = (...args: Parameters<typeof settings>) => {
    initial.settingsDrafts = useCrmStore.getState().settingsDrafts;
    return settings(...args);
  };

  it("comes back with the member, until a re-read replaces the row", () => {
    const store = useCrmStore.getState();
    const cases = [
      { key: `status:deal:${STATUS.id}`, base: STATUS, value: { ...STATUS, name: "Qualified draft" }, text: "Qualified draft", admin: false, mode: "native" as CrmMode, section: "stages" as const },
      { key: `overlay:deal:${STATUS.id}`, base: STATUS, value: { ...STATUS, ui_probability: 42 }, text: "42", admin: true, mode: "mirror" as CrmMode, section: "stages" as const },
      { key: `reason:${REASON.id}`, base: REASON.label, value: "Price draft", text: "Price draft", admin: false, mode: "native" as CrmMode, section: "lost-reasons" as const },
    ];
    try {
      for (const c of cases) {
        store.setSettingsDraft(c.key, { base: c.base, value: c.value });
        expect(typed(settingsNow(c.admin, c.mode, c.section), c.text), c.key).toBe(true);
        // Another section unmounts the row. The store keeps the draft.
        expect(typed(settingsNow(c.admin, c.mode, "lead-statuses"), c.text), c.key).toBe(false);
        expect(typed(settingsNow(c.admin, c.mode, c.section), c.text), c.key).toBe(true);
      }
      // A re-read brings new rows, and the drafts no longer apply.
      expect(typed(settingsNow(false, "native", "stages", { deal: { ...STATUS } }), "Qualified draft")).toBe(false);
      expect(typed(settingsNow(false, "native", "lost-reasons", { reason: { ...REASON } }), "Price draft")).toBe(true);
      expect(draftOf({ base: STATUS, value: "x" }, { ...STATUS })).toEqual({ ...STATUS });
    } finally {
      useCrmStore.setState({ settingsDrafts: {} });
      initial.settingsDrafts = {};
    }
  });

  it("is dropped when the member types the served value back", () => {
    useCrmStore.getState().setSettingsDraft("new-reason", null);
    expect(useCrmStore.getState().settingsDrafts).toEqual({});
    expect(read("./PipelineSettings.tsx")).toContain(
      "setDraft(key, Object.is(next, served) ? null : { base: served, value: next })",
    );
  });
});

// ── Review: one name for one thing ─────────────────────────────────────────

describe("the card headings", () => {
  it("are the labels of the rail rows", () => {
    for (const isAdmin of [true, false]) {
      for (const mode of MODES) {
        for (const section of visibleSections({ isAdmin, mode, studioAdmin: true })) {
          if (section.id === "setup" || section.id === "access") continue;
          const html = settings(isAdmin, mode, section.id as SettingsSectionId, {}, true);
          expect(html, `${section.id} ${isAdmin} ${mode}`).toContain(`>${section.label}</h2>`);
        }
      }
    }
    // The page passes these two cards in, so their source holds the heading.
    expect(read("./SetupHub.tsx")).toContain(`>${sectionLabel("setup")}</h2>`);
    expect(read("./AccessPanel.tsx")).toContain(`>${sectionLabel("access")}</h2>`);
    expect(FIELD_MAP_TITLE).toBe(sectionLabel("field-mapping"));
    expect(settings(true, "native")).not.toContain("Pipelines and stages");
  });
});

// ── Item 9: the title bar names the section ────────────────────────────────

describe("the title bar", () => {
  it("names the section on screen", () => {
    expect(read("../page.tsx")).toMatch(/`Settings · \$\{sectionLabel\(settingsSection\)\}`/);
  });
});

describe("a saved draft leaves the store (CRM-C1 re-review)", () => {
  // Kept, a saved draft comes back when another admin sets the served value
  // back to the draft's base. A Save would then overwrite the newer value.
  const source = read("./PipelineSettings.tsx");

  it("each row clears its draft after a save that succeeds", () => {
    const saves = source.match(/if \(await onSave\(.*?\)\) clearDraft\(\);/g) ?? [];
    expect(saves).toHaveLength(3);
    for (const key of ["status:", "overlay:", "reason:"]) {
      const at = source.indexOf("= useSettingsDraft(`" + key);
      expect(at, key).toBeGreaterThan(0);
      // The line that takes the draft of this row also takes its clear.
      const line = source.slice(source.lastIndexOf("\n", at), at);
      expect(line, key).toContain("clearDraft]");
    }
  });

  it("the hook's clear writes null for its key", () => {
    expect(source).toMatch(/\(\) => setDraft\(key, null\)/);
  });
});
