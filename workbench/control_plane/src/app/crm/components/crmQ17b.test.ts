// Getting around (WS-53 CRM-Q17b, `crm_platform.md` section 18.9.6). The
// runner has no DOM, so each part is rendered to a string and read as text.
//
// Done when:
// 1. The custom modules fold into one rail group, with the modules that hold
//    records first. Reports and CRM settings stay in view at 1440 x 900.
// 2. A core row that Zoho turned off and that holds no records leaves the rail.
// 3. `/crm` opens on the first row that holds records.
// 4. An entity with no records says why. "Nothing matches this view" shows
//    only with a filter.
// (Items 5 to 8 have their own blocks below.)
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { CrmModule } from "../lib/modules";
import { DEFAULT_VIEW } from "../lib/urlState";
import {
  MODULE_GROUP_LABEL,
  NOTHING_MATCHES,
  coreStatesOf,
  emptyListLine,
  hiddenCoreRow,
  landing,
  railEntries,
  railSections,
  type CoreStates,
} from "../lib/views";
import CrmRail from "./CrmRail";
import FilterBar from "./FilterBar";
import KanbanBoard from "./KanbanBoard";

const noop = () => undefined;
const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf-8");

const mod = (api_name: string, label: string, count = 0): CrmModule => ({
  api_name,
  label,
  singular_label: null,
  count,
});

/** DEWiN's shape: empty modules between full ones, in the profile order. */
const MODULES = [
  mod("Commercials", "Commercials", 0),
  mod("Operator_Associates", "Operator Associates", 127),
  mod("Quotes", "Quotes", 0),
  mod("Invoices", "Invoices", 31924),
];

/** DEWiN: Deals turned off in Zoho and empty. Contacts holds the sites. */
const DEWIN: CoreStates = {
  leads: { records: false, turned_off: false },
  contacts: { records: true, turned_off: false },
  organizations: { records: true, turned_off: false },
  deals: { records: false, turned_off: true },
};

const names = (entries: ReturnType<typeof railEntries>) =>
  entries.map((e) => (e.kind === "view" ? e.view.label : e.row.label));

// ── Item 1: one group of modules ────────────────────────────────────────────

describe("item 1: the custom modules fold into one rail group", () => {
  it("splits the rail into the core rows, one group, and the rows after it", () => {
    const sections = railSections(railEntries(MODULES));
    expect(sections.head.map((e) => (e.kind === "view" ? e.view.id : ""))).toEqual([
      "leads",
      "contacts",
      "organizations",
      "deals",
    ]);
    expect(sections.tail.map((e) => (e.kind === "view" ? e.view.id : ""))).toEqual(["reports"]);
  });

  it("puts the modules that hold records first, each part in the order of the gateway", () => {
    const { modules } = railSections(railEntries(MODULES));
    expect(modules.map((m) => m.module)).toEqual([
      "Operator_Associates",
      "Invoices",
      "Commercials",
      "Quotes",
    ]);
  });

  const rail = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "deals",
        onSelect: noop,
        modules: { modules: MODULES, active: null, onSelect: noop },
        ...over,
      }),
    );

  it("draws the group folded, so Reports and CRM settings stay in view", () => {
    const html = rail();
    expect(html).toContain(`>${MODULE_GROUP_LABEL}<`);
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain(">Invoices<");
    // The order: the core rows, the group, Reports, then CRM settings.
    const at = (text: string) => html.indexOf(`>${text}<`);
    expect(at("Deals")).toBeLessThan(at(MODULE_GROUP_LABEL));
    expect(at(MODULE_GROUP_LABEL)).toBeLessThan(at("Reports"));
    expect(at("Reports")).toBeLessThan(at("CRM settings"));
  });

  it("opens the group while one of its modules is on screen, in a bounded box", () => {
    const html = rail({ tab: "module", modules: { modules: MODULES, active: "Invoices", onSelect: noop } });
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain(">Invoices<");
    expect(html).toMatch(/max-h-64[^"]*overflow-y-auto/);
    expect(html.indexOf(">Operator Associates<")).toBeLessThan(html.indexOf(">Commercials<"));
  });

  it("draws no group with no module", () => {
    const html = rail({ modules: { modules: [], active: null, onSelect: noop } });
    expect(html).not.toContain(MODULE_GROUP_LABEL);
  });
});

// ── Item 2: a turned-off, empty core row leaves ────────────────────────────

describe("item 2: a core row that Zoho turned off and that holds no records leaves the rail", () => {
  it("drops Deals for DEWiN, and keeps each other row", () => {
    expect(names(railEntries([], null, null, DEWIN))).toEqual([
      "Leads",
      "Contacts",
      "Companies",
      "Reports",
    ]);
  });

  it("keeps a turned-off row that still holds records, and a row that a module plays", () => {
    expect(hiddenCoreRow("deals", { deals: { records: true, turned_off: true } })).toBe(false);
    expect(hiddenCoreRow("deals", DEWIN, { deals: "Commercials" })).toBe(false);
    expect(hiddenCoreRow("leads", DEWIN)).toBe(false); // empty, but on in Zoho
    expect(hiddenCoreRow("deals", DEWIN)).toBe(true);
  });

  it("keeps each row with no state from the gateway", () => {
    expect(names(railEntries([], null, null, null))).toContain("Deals");
    expect(hiddenCoreRow("deals", null)).toBe(false);
  });

  it("reads the gateway's block with care", () => {
    expect(coreStatesOf(undefined)).toBeNull();
    expect(coreStatesOf({})).toBeNull();
    expect(
      coreStatesOf({
        deals: { records: false, turned_off: true },
        users: { records: true, turned_off: false },
        leads: { records: "yes", turned_off: false },
      }),
    ).toEqual({ deals: { records: false, turned_off: true } });
  });

  it("hands the states to both rails of the page", () => {
    expect(read("../page.tsx").match(/states=\{coreStates\}/g)?.length).toBe(2);
  });
});

// ── Item 3: where /crm opens ────────────────────────────────────────────────

describe("item 3: /crm opens on the first row that holds records", () => {
  it("opens Contacts for DEWiN, never the empty Deals board", () => {
    const entries = railEntries(MODULES, null, null, DEWIN);
    expect(landing(entries, DEWIN, MODULES)).toEqual({ kind: "view", tab: "contacts" });
  });

  it("follows the rail order: Leads first when Leads holds records", () => {
    const states: CoreStates = { ...DEWIN, leads: { records: true, turned_off: false } };
    expect(landing(railEntries(MODULES, null, null, states), states, MODULES)).toEqual({
      kind: "view",
      tab: "leads",
    });
  });

  it("opens the module that plays a row, by the count of that module", () => {
    const states: CoreStates = { leads: { records: false, turned_off: false } };
    const overlays = { leads: "Operator_Associates" };
    const entries = railEntries(MODULES, null, overlays, states);
    expect(landing(entries, states, MODULES, overlays)).toEqual({
      kind: "module",
      module: "Operator_Associates",
    });
  });

  it("opens the first module that holds records when no core row does", () => {
    const empty: CoreStates = {
      leads: { records: false, turned_off: false },
      contacts: { records: false, turned_off: false },
      organizations: { records: false, turned_off: false },
      deals: { records: false, turned_off: false },
    };
    expect(landing(railEntries(MODULES, null, null, empty), empty, MODULES)).toEqual({
      kind: "module",
      module: "Operator_Associates",
    });
    expect(landing(railEntries([], null, null, empty), empty, [])).toBeNull();
  });

  it("with no states, opens the module that plays Deals (Batch M review), else stays", () => {
    const overlays = { deals: "Commercials" };
    expect(landing(railEntries(MODULES, null, overlays), null, MODULES, overlays)).toEqual({
      kind: "module",
      module: "Commercials",
    });
    expect(landing(railEntries(MODULES), null, MODULES, {})).toBeNull();
  });

  it("waits for the answer before it loads a list, and replaces the address", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/if \(landingPending\) return;/);
    expect(page).toMatch(/search === "" && ctx\.mode === "mirror"/);
    expect(page).toMatch(/replaceView\(landingHref\)/);
    expect(page).toMatch(/landingPending \? \(/);
  });
});

// ── Item 4: an empty entity says why ────────────────────────────────────────

describe("item 4: an entity with no records says why", () => {
  it("says 'Nothing matches this view' only with a filter", () => {
    expect(emptyListLine("Deals", { filtered: true, mirror: true, turnedOff: true })).toBe(NOTHING_MATCHES);
    expect(emptyListLine("Deals", { filtered: false, mirror: true, turnedOff: false })).not.toBe(
      NOTHING_MATCHES,
    );
    expect(emptyListLine("Deals", { filtered: false, mirror: false, turnedOff: false })).not.toBe(
      NOTHING_MATCHES,
    );
  });

  it("names a module that Zoho turned off, or says that no record exists yet", () => {
    expect(emptyListLine("Deals", { filtered: false, mirror: true, turnedOff: true })).toBe(
      "Deals is turned off in your Zoho.",
    );
    expect(emptyListLine("Deals", { filtered: false, mirror: false, turnedOff: false })).toBe("No records yet.");
    expect(emptyListLine("Existing Sites", { filtered: false, mirror: true, turnedOff: false })).toBe(
      "Your Zoho sent no existing sites to this CRM yet.",
    );
  });

  it("draws the line on an empty board, in place of 'No deals match this view'", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, {
        lanes: [],
        hiddenEmpty: 6,
        loading: false,
        onMove: noop,
        onOpen: noop,
        emptyNote: "Deals is turned off in your Zoho.",
      }),
    );
    expect(html).toContain("Deals is turned off in your Zoho.");
    expect(html).not.toContain("No deals match this view.");
  });

  it("gives the list and the board the one rule", () => {
    const page = read("../page.tsx");
    expect(page).toContain("emptyNote={emptyLineOf(listEntity)}");
    expect(page).toMatch(/emptyLineOf\(view\.tab === "leads" \? "leads" : "deals"\)/);
    expect(page).not.toContain("memberEmptyLine(");
  });
});

// ── Batch M review: the search box names the list ───────────────────────────

describe("the search box names the list on screen (Batch M review)", () => {
  const bar = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "contacts",
        view: { ...DEFAULT_VIEW, tab: "contacts", layout: "list" },
        onChange: noop,
        owners: null,
        fieldDefs: null,
        search: true,
        activeView: null,
        dirty: false,
        canEdit: false,
        isAdmin: false,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
        ...over,
      }),
    );

  it("says 'existing sites' where the source names Contacts so", () => {
    const html = bar({ labels: { contacts: { label: "Existing Sites" } } });
    expect(html).toContain('data-page-filter="existing sites"');
    expect(html).not.toContain('data-page-filter="contacts"');
  });

  it("keeps the CRM's name with no source label", () => {
    expect(bar()).toContain('data-page-filter="contacts"');
  });
});
