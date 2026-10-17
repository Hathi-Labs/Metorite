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
// 5. The filter bar shows Owner and the 3 fullest picklist filters. The rest
//    sit behind "Filters (n)".
// 6. The Columns picker has a search box, shows the chosen fields first, and
//    shows "% filled" beside each field. A disabled box looks disabled.
// 7. At phone width, each list row is a two-line card.
// 8. Sync health opens with a "Needs you" block: held, large and turned-off
//    modules, each with one action. The Sync switch of a turned-off module is
//    disabled. The detail table folds.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { FieldDef } from "../lib/fields";
import { fieldMatches, fillLabel } from "../lib/fields";
import { SHOWN_FILTERS, hiddenActiveCount, moreFiltersLabel, splitFilters } from "../lib/filterBar";
import type { CrmModule } from "../lib/modules";
import type { OverlayPreview, Suggestion } from "../lib/suggestions";
import {
  HIDDEN_SENTENCE,
  NEEDS_YOU_ACTIONS,
  isTurnedOff,
  needsYou,
  type HealthEntity,
  type SyncHealth,
} from "../lib/syncHealth";
import { DEFAULT_VIEW } from "../lib/urlState";
import {
  MODULE_GROUP_LABEL,
  NOTHING_MATCHES,
  coreStatesOf,
  emptyListLine,
  hiddenCoreRow,
  landing,
  landingPlan,
  railEntries,
  railSections,
  type CoreStates,
} from "../lib/views";
import CrmRail from "./CrmRail";
import FilterBar from "./FilterBar";
import KanbanBoard from "./KanbanBoard";
import { CARD_DETAILS, CHOSEN_HEADING, ColumnChoices, FULL_TIP, RecordCards } from "./RecordList";
import { SuggestionPanelBody } from "./Suggestions";
import { SyncHealthCardView } from "./SyncHealthCard";

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
    expect(page).toMatch(/replaceView\(plan\.redirect\)/);
    expect(page).toMatch(/landingPending \? \(/);
    // The page keeps the decision for its mount, and feeds it back.
    expect(page).toMatch(/decided: landed,/);
    expect(page).toMatch(/if \(plan\.decide\) setLanded\(true\);/);
  });
});

// ── Fix round 1, P1: the landing happens once for each mount ───────────────

/**
 * The page as a loop over its renders: each render asks `landingPlan`, keeps
 * `decided` as the page does, and follows a redirect as `replaceView` does.
 * `target` is what `landing` gives for the state of that render.
 */
function navigator(target: () => string | null) {
  let decided = false;
  let search = "";
  const render = (known = true) => {
    const plan = landingPlan({
      decided,
      search,
      mirror: true,
      known,
      target: target(),
      here: search ? `/crm?${search}` : "/crm",
    });
    if (plan.decide) decided = true;
    if (plan.redirect) {
      search = plan.redirect.replace(/^\/crm\??/, "");
      render(known);
    }
    return plan;
  };
  return {
    render,
    go: (next: string) => {
      search = next;
      return render();
    },
    get address() {
      return search ? `/crm?${search}` : "/crm";
    },
  };
}

describe("fix round 1, P1: /crm lands once for each mount of the page", () => {
  it("waits while the reads run, then lands on Leads", () => {
    const nav = navigator(() => "/crm?tab=leads");
    expect(nav.render(false).pending).toBe(true);
    expect(nav.address).toBe("/crm");
    nav.render(true);
    expect(nav.address).toBe("/crm?tab=leads");
  });

  it("keeps the member on Deals after a click, though Deals is a bare /crm", () => {
    const nav = navigator(() => "/crm?tab=leads");
    nav.render();
    expect(nav.address).toBe("/crm?tab=leads");
    // The URL grammar writes no `tab` for the Deals board.
    const plan = nav.go("");
    expect(plan).toEqual({ decide: false, redirect: null, pending: false });
    expect(nav.address).toBe("/crm");
  });

  it("does not move the member when the mode is read again", () => {
    let target: string | null = "/crm?tab=leads";
    const nav = navigator(() => target);
    nav.render();
    nav.go("");
    // A new read of `GET /crm/mode` gives another first row.
    target = "/crm?tab=contacts";
    expect(nav.render().redirect).toBeNull();
    expect(nav.address).toBe("/crm");
  });

  it("never lands when the page opened on an address with a view", () => {
    const nav = navigator(() => "/crm?tab=leads");
    expect(nav.go("tab=reports")).toEqual({ decide: true, redirect: null, pending: false });
    expect(nav.go("")).toEqual({ decide: false, redirect: null, pending: false });
  });

  it("decides at once when the landing is the bare address itself", () => {
    expect(
      landingPlan({ decided: false, search: "", mirror: true, known: true, target: "/crm", here: "/crm" }),
    ).toEqual({ decide: true, redirect: null, pending: false });
    expect(
      landingPlan({ decided: false, search: "", mirror: false, known: false, target: null, here: "/crm" }),
    ).toEqual({ decide: true, redirect: null, pending: false });
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

  it("says why on a Mirror board with no stage, and keeps the stage hint in Native", () => {
    const board = (readOnly: boolean) =>
      renderToStaticMarkup(
        createElement(KanbanBoard, {
          lanes: [],
          loading: false,
          readOnly,
          onMove: noop,
          onOpen: noop,
          emptyNote: "Deals is turned off in your Zoho.",
        }),
      );
    expect(board(true)).toContain("Deals is turned off in your Zoho.");
    expect(board(true)).not.toContain("No deal stages are configured yet.");
    expect(board(false)).toContain("No deal stages are configured yet.");
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

// ── Item 5: the filter bar folds ────────────────────────────────────────────

const pick = (key: string, fill: number | null, position: number): FieldDef => ({
  key,
  label: key.charAt(0).toUpperCase() + key.slice(1),
  type: "select",
  options: ["a", "b"],
  position,
  visible: true,
  source: "zoho",
  api_name: key,
  fill,
});

describe("item 5: the filter bar shows Owner and the 3 fullest picklists", () => {
  const defs = [
    pick("region", 0.2, 0),
    pick("tier", 0.9, 1),
    pick("segment", 0.5, 2),
    pick("channel", 0.9, 3),
    pick("zone", 0.1, 4),
  ];
  const filters = defs.map((def) => ({ def, kind: "options" as const }));

  it("takes the fullest picklists, a tie in the order of the bar", () => {
    const { shown, more } = splitFilters(filters);
    expect(shown.map((f) => f.def.key)).toEqual(["tier", "segment", "channel"]);
    expect(more.map((f) => f.def.key)).toEqual(["region", "zone"]);
    expect(SHOWN_FILTERS).toBe(3);
  });

  it("takes the first picklists with no fill number, and never a checkbox at rest", () => {
    const flat = defs.map((def) => ({ def: { ...def, fill: null }, kind: "options" as const }));
    expect(splitFilters(flat).shown.map((f) => f.def.key)).toEqual(["region", "tier", "segment"]);
    const box = { def: { ...pick("signed", 1, 9), type: "boolean" }, kind: "boolean" as const };
    expect(splitFilters([box, ...filters]).more).toContain(box);
  });

  it("counts the filters in force behind the button", () => {
    const { more } = splitFilters(filters);
    expect(hiddenActiveCount(DEFAULT_VIEW, more)).toBe(0);
    const view = { ...DEFAULT_VIEW, source: "email", ranges: { updated_from: "2026-10-01" }, custom: { zone: ["a"] } };
    expect(hiddenActiveCount(view, more)).toBe(3);
    expect(moreFiltersLabel(0)).toBe("Filters");
    expect(moreFiltersLabel(3)).toBe("Filters (3)");
  });

  it("draws the three at rest, and Source and the rest behind the button", () => {
    const html = renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "leads",
        view: { ...DEFAULT_VIEW, tab: "leads", layout: "list" },
        onChange: noop,
        owners: null,
        fieldDefs: defs,
        activeView: null,
        dirty: false,
        canEdit: false,
        isAdmin: false,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
      }),
    );
    for (const word of ["Owner", "Tier", "Segment", "Channel", ">Filters<", 'aria-expanded="false"']) {
      expect(html).toContain(word);
    }
    for (const word of ["Region", "Zone", "Source", "Updated"]) expect(html).not.toContain(word);
  });
});

// ── Item 6: the Columns picker ──────────────────────────────────────────────

describe("item 6: the Columns picker", () => {
  const defs = [pick("region", 0.2, 0), pick("tier", 0.94, 1), pick("segment", null, 2)];
  const draw = (chosen: string[]) =>
    renderToStaticMarkup(
      createElement(ColumnChoices, {
        groups: [{ section: null, fields: defs }],
        chosen,
        onToggle: noop,
      }),
    );

  it("has a search box", () => {
    expect(draw([])).toContain('aria-label="Find a field"');
    expect(fieldMatches(defs[1], "TI")).toBe(true);
    expect(fieldMatches(defs[1], "zone")).toBe(false);
    expect(fieldMatches(defs[1], "  ")).toBe(true);
  });

  it("shows the chosen fields first, under their own heading", () => {
    const html = draw(["segment"]);
    expect(html).toContain(CHOSEN_HEADING);
    expect(html.indexOf(">Segment<")).toBeLessThan(html.indexOf(">Region<"));
    expect(draw([])).not.toContain(CHOSEN_HEADING);
  });

  it("shows '% filled' beside each field with a number", () => {
    const html = draw([]);
    expect(html).toContain("94% filled");
    expect(html).toContain("20% filled");
    expect(fillLabel(defs[2])).toBeNull();
    expect(fillLabel({ ...defs[0], fill: 0.001 })).toBe("1% filled");
    expect(fillLabel({ ...defs[0], fill: 0 })).toBe("0% filled");
  });

  it("makes a disabled box look disabled", () => {
    const keys = ["a", "b", "c", "d", "e"];
    const full = [...keys.map((k, i) => pick(k, 1, 10 + i)), pick("region", 0.2, 0)];
    const html = renderToStaticMarkup(
      createElement(ColumnChoices, { groups: [{ section: null, fields: full }], chosen: keys, onToggle: noop }),
    );
    expect(html).toMatch(/class="[^"]*cursor-not-allowed text-muted-foreground"[^>]*title="[^"]*Clear one first/);
    expect(html).toContain("disabled:bg-muted");
    expect(FULL_TIP).toContain("Clear one first");
  });
});

// ── Item 7: a two-line card at phone width ──────────────────────────────────

describe("item 7: at phone width each list row is a two-line card", () => {
  const rows = [
    { id: "c1", first_name: "Ravi", last_name: "Kumar", email: "ravi@dewin.test", phone: "", owner_email: null },
  ];
  const columns = [
    { key: "first_name", label: "Name" },
    { key: "email", label: "Email" },
    { key: "phone", label: "Phone" },
    { key: "owner_email", label: "Owner" },
  ];

  it("draws the name, then the next filled cells on one line, as one button", () => {
    const html = renderToStaticMarkup(
      createElement(RecordCards, {
        rows,
        columns,
        extra: [],
        ctx: { statuses: [], mode: "mirror" },
        onOpen: noop,
      }),
    );
    expect(html).toMatch(/<button type="button"[^>]*>/);
    expect(html).toContain("Ravi Kumar");
    expect(html).toContain("ravi@dewin.test");
    // Two lines: the name and one line of details, each cut, never wrapped.
    expect(html.match(/class="block w-full truncate/g)?.length).toBe(1);
    expect(html).toContain("whitespace-nowrap");
    expect(CARD_DETAILS).toBe(3);
  });

  it("draws the cards on a phone, and the table elsewhere", () => {
    const list = read("./RecordList.tsx");
    expect(list).toMatch(/isMobile \? \(\s*<RecordCards/);
  });
});

// ── Item 8: "Needs you" on Sync health ──────────────────────────────────────

const entity = (key: string, over: Partial<HealthEntity> = {}): HealthEntity => ({
  key,
  zoho_count: 10,
  ours: 10,
  last_run: {},
  ...over,
});

const health = (over: Partial<SyncHealth> = {}): SyncHealth => ({
  last_run: { kind: "incremental", status: "ok", started_at: null, finished_at: null, error: null, credits_used: 4 },
  last_success_at: null,
  next_run_at: null,
  entities: [
    entity("contacts"),
    entity("deals", { zoho_count: null, ours: 0, last_run: { unavailable: 1, hidden: 1 } }),
    entity("module:Invoices", { zoho_count: 31924, ours: 0 }),
    entity("module:Payment_Received", { zoho_count: 8681, ours: 0 }),
  ],
  modules: [
    { api_name: "Invoices", label: "Invoices", custom: false, api_supported: true, mirrored: true },
    { api_name: "Payment_Received", label: "Payment Received", custom: true, api_supported: true, mirrored: true },
  ],
  history: [],
  running: false,
  credits: { budget_24h: 500, used_24h: 20 },
  holds: { "module:Invoices": { held: true, by: "auto", count: 31924, at: null } },
  ...over,
});

describe("item 8: Sync health opens with a 'Needs you' block", () => {
  it("lists the turned-off, held and large modules, each with one action", () => {
    const items = needsYou(health());
    expect(items.map((i) => [i.key, i.kind, i.action])).toEqual([
      ["deals", "unavailable", "check-again"],
      ["module:Invoices", "held", "sync-alone"],
      ["module:Payment_Received", "large", "hold"],
    ]);
    expect(items[0].text).toBe(HIDDEN_SENTENCE);
    expect(items[1].text).toBe("Invoices has 31,924 records in Zoho. Sync it on its own when Metorite is quiet, so the rest stays fast");
    expect(NEEDS_YOU_ACTIONS["check-again"]).toBe("Check again");
  });

  it("lists a key once, and nothing for a healthy sync", () => {
    const both = health({
      entities: [entity("module:Invoices", { zoho_count: 31924, ours: 0, last_run: { unavailable: 1, hidden: 1 } })],
    });
    expect(needsYou(both).map((i) => i.kind)).toEqual(["unavailable"]);
    expect(needsYou(health({ entities: [entity("contacts")], holds: {} }))).toEqual([]);
    expect(needsYou(null)).toEqual([]);
  });

  const draw = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(SyncHealthCardView, { health: health(), nowMs: 0, onSyncNow: noop, ...over }),
    );

  it("draws the block first, with one button for each item", () => {
    const html = draw();
    expect(html).toContain('aria-label="Needs you"');
    expect(html.indexOf("Needs you")).toBeLessThan(html.indexOf("Credits used"));
    for (const word of ["Check again", "Sync it on its own", "Hold it", HIDDEN_SENTENCE]) {
      expect(html).toContain(word);
    }
  });

  it("folds the detail table", () => {
    const html = draw();
    expect(html).toContain("Show the details");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("<table");
    expect(html).not.toContain("Modules in your Zoho");
    expect(draw({ detailsOpen: true })).toContain("<table");
  });

  it("disables the Sync switch of a turned-off module", () => {
    const html = draw({ detailsOpen: true });
    const box = (name: string) => html.match(new RegExp(`<input[^>]*aria-label="Sync ${name}"[^>]*>`))?.[0] ?? "";
    expect(box("Deals")).toContain('disabled=""');
    expect(box("Contacts")).not.toContain('disabled=""');
    expect(isTurnedOff(health().entities[1])).toBe(true);
    // A missing scope is unavailable, and not turned off.
    expect(isTurnedOff(entity("tasks", { last_run: { unavailable: 1 } }))).toBe(false);
  });

  it("reads the one short-read rule of lib/syncHealth.ts (Batch M review)", () => {
    const card = read("./SyncHealthCard.tsx");
    expect(card).toContain("shortReadSentence(entity)");
    expect(card).not.toMatch(/short_by|zoho_count\s*-|-\s*entity\.ours/);
  });
});

// ── Batch M review: the preview at 375 px ───────────────────────────────────

describe("the module preview does not scroll sideways at phone width (Batch M review)", () => {
  const suggestion: Suggestion = {
    slot: "deal",
    version: 7,
    module: "Commercials",
    label: "Commercials",
    records: 24,
    targets: [{ target: "name", fields: ["Commercial_Name"], labels: ["Commercial Name"], unmapped: 0 }],
    stage: null,
  };
  const preview: OverlayPreview = {
    slot: "deal",
    module: "Commercials",
    columns: ["name", "probability", "stage"],
    rows: [{ id: "r1", values: { name: "A very long commercial name for a site", probability: 40, stage: "won" } }],
    total: 24,
    unmapped: {},
    unsorted: 0,
  };
  const html = renderToStaticMarkup(
    createElement(SuggestionPanelBody, { suggestion, listLabel: "Deals", preview }),
  );

  it("stacks each record below sm, and keeps the table from sm up", () => {
    expect(html).toMatch(/<ul class="space-y-2 sm:hidden"/);
    expect(html).toMatch(/class="hidden overflow-x-auto rounded-lg border border-border sm:block"/);
    // The stacked block cuts a long value, so no row grows past the panel.
    expect(html).toMatch(/<dd class="min-w-0 flex-1 truncate/);
    const from = html.indexOf('<ul class="space-y-2 sm:hidden"');
    const stacked = html.slice(from, html.indexOf("</ul>", from));
    expect(stacked).toContain("A very long commercial name for a site");
    expect(stacked).toContain("40");
  });
});
