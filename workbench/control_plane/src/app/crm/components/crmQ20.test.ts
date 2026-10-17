// The suggestion, the panel, the overlay and Undo (WS-53 CRM-Q20-2, spec
// crm_platform.md section 18.9.6). The runner has no DOM, so each component
// is rendered to a string, and a wire that only the page holds is read from
// its source.
import { readFileSync } from "node:fs";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Button from "@/components/ui/Button";
import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import { visibleSections } from "../lib/settingsSections";
import {
  DISMISS_LABEL,
  NO_SUGGESTIONS,
  SUGGESTION_MOVED,
  UNDO_LABEL,
  checks,
  dismissedLine,
  emptyLine,
  facts,
  memberEmptyLine,
  overlaysBySlug,
  previewLabel,
  slotOf,
  suggestionError,
  suggestionFor,
  undoneLine,
  useLabel,
  usedLine,
  whatChanges,
  type OverlayPreview,
  type Suggestion,
  type SuggestionList,
} from "../lib/suggestions";
import { railEntries } from "../lib/views";
import CrmRail from "./CrmRail";
import RecordList from "./RecordList";
import { SuggestionsCardView, type SuggestionsHandlers } from "./SettingsCards";
import { SlotSuggestion, SuggestionPanelBody } from "./Suggestions";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const noop = () => undefined;

// ── Fixtures ────────────────────────────────────────────────────────────────

const SUGGESTION: Suggestion = {
  slot: "deal",
  version: 7,
  module: "Commercials",
  label: "Commercials",
  records: 24,
  targets: [
    { target: "name", fields: ["Commercial_Name"], labels: ["Commercial Name"], unmapped: 0 },
    { target: "probability", fields: ["Probability"], labels: ["Probability"], unmapped: 2 },
  ],
  stage: {
    field: "Status",
    label: "Status",
    values: { Won: "won", Lost: "lost", Open: "open", "On Hold": "open" },
    unsorted: 1,
  },
};

const PREVIEW: OverlayPreview = {
  slot: "deal",
  module: "Commercials",
  columns: ["name", "probability", "stage"],
  rows: [
    { id: "r1", values: { name: "Commercials 0", probability: 40, stage: "won" } },
    { id: "r2", values: { name: "Commercials 1", probability: null, stage: null } },
  ],
  total: 24,
  unmapped: { name: 0, probability: 3 },
  unsorted: 1,
};

const LIST: SuggestionList = {
  suggestions: [SUGGESTION],
  overlays: [{ slot: "company", module: "Customers", label: "Customers" }],
};

const MODULES = [
  { api_name: "Commercials", label: "Commercials", singular_label: "Commercial", count: 24 },
  { api_name: "Visits", label: "Visits", singular_label: "Visit", count: 3 },
];

const handlers = (over: Partial<SuggestionsHandlers> = {}): SuggestionsHandlers => ({
  listLabel: (slug) => ({ deals: "Deals", organizations: "Companies", leads: "Leads", contacts: "Contacts" })[slug],
  onPreview: noop,
  onDismiss: noop,
  onUndo: noop,
  busy: null,
  ...over,
});

/** Every element in a tree that a function component returned. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

function buttonOf(tree: ReactNode, label: string) {
  return elements(tree).find(
    (el) => el.type === Button && (el.props as { children?: ReactNode }).children === label,
  ) as ReactElement<{ onClick?: () => void }> | undefined;
}

/** No engine word and no confidence number (section 18.9.2). */
const ENGINE_WORDS =
  /\bdraft\b|\bversion\b|\brole\b|\bslot\b|overlay|value_map|\bplan\b|\d+\s*%|0\.\d|confiden|\bsure\b|\bmodule\b/i;

/** The text a person reads, and the names a reader of the screen hears. */
const textOf = (html: string) =>
  [
    html.replace(/<[^>]+>/g, " "),
    ...[...html.matchAll(/(?:aria-label|title)="([^"]*)"/g)].map((m) => m[1]),
  ].join(" ");

// ── Option A: the overlay of GET /crm/mode, and the rail ─────────────────────

describe("the rail row of a list that a Zoho module plays (option A)", () => {
  it("reads the overlays of the mode by list, and drops a word it does not know", () => {
    expect(overlaysBySlug({ deal: "Commercials", company: "Customers" })).toEqual({
      deals: "Commercials",
      organizations: "Customers",
    });
    const junk = { deal: "", nope: "X", lead: 4 } as unknown as Record<string, string>;
    expect(overlaysBySlug(junk)).toEqual({});
    expect(overlaysBySlug(undefined)).toEqual({});
    expect(slotOf("deals")).toBe("deal");
    expect(slotOf("reports")).toBeNull();
  });

  it("keeps the label of the list, opens the module, and drops the module's own row", () => {
    const entries = railEntries(MODULES, null, { deals: "Commercials" });
    const deals = entries.find((e) => e.kind === "view" && e.view.id === "deals");
    expect(deals).toEqual({
      kind: "view",
      view: { id: "deals", label: "Deals", icon: "Handshake" },
      module: "Commercials",
    });
    const modules = entries.filter((e) => e.kind === "module").map((e) => e.kind === "module" && e.row.module);
    expect(modules).toEqual(["Visits"]);
    // With no overlay, the rail is the one of before.
    const before = railEntries(MODULES, null);
    expect(before.find((e) => e.kind === "view" && e.view.id === "deals")).not.toHaveProperty("module");
    expect(before.filter((e) => e.kind === "module")).toHaveLength(2);
  });

  it("highlights the Deals row while the module's list is on screen", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "module",
        onSelect: noop,
        modules: { modules: MODULES, active: "Commercials", onSelect: noop },
        overlays: { deals: "Commercials" },
      }),
    );
    const current = [...html.matchAll(/aria-current="page"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => m[1]);
    expect(current.join(" ")).toContain("Deals");
    expect(html).not.toContain(">Commercials<");
  });

  it("wires the overlays from the mode to both rails, for each member", () => {
    const page = read("../page.tsx");
    expect(page).toContain('const overlays = useOverlays(ctx.mode === "mirror");');
    expect(page.match(/overlays=\{overlays\}/g)?.length).toBe(2);
    const rail = read("./CrmRail.tsx");
    expect(rail).toContain("module ? modules?.onSelect(module) : onSelect(view.id)");
    // One read of GET /crm/mode serves the labels and the overlays.
    const hooks = read("../lib/useCrmMode.ts");
    expect(hooks.match(/"crm:mode"/g)?.length).toBe(1);
  });
});

// ── Item 5: the suggestion in the empty list ────────────────────────────────

describe("the suggestion in an empty list (item 5)", () => {
  it("names the list, the module and its records, with one action", () => {
    const html = renderToStaticMarkup(
      createElement(SlotSuggestion, { suggestion: SUGGESTION, listLabel: "Deals", onPreview: noop }),
    );
    expect(previewLabel(SUGGESTION, "Deals")).toBe("Preview Commercials as Deals");
    expect(html).toContain("Preview Commercials as Deals");
    expect(html).toContain(emptyLine(SUGGESTION, "Deals"));
    expect(textOf(html)).not.toMatch(ENGINE_WORDS);
  });

  it("finds the suggestion of the list on screen only", () => {
    expect(suggestionFor(LIST, "deals")).toBe(SUGGESTION);
    expect(suggestionFor(LIST, "leads")).toBeNull();
    expect(suggestionFor(null, "deals")).toBeNull();
  });

  it("reads the suggestions for an admin in Mirror mode only", () => {
    const page = read("../page.tsx");
    expect(page).toContain('useSuggestions(ctx.isAdmin && ctx.mode === "mirror")');
    expect(page).toMatch(/\{slotSuggestion \? \(\s*\/\/[\s\S]*?<SlotSuggestionArea/);
  });

  it("tells a member why the list is empty, with no suggestion", () => {
    const line = memberEmptyLine("Deals");
    expect(line).toBe("Your Zoho sent no deals to this CRM yet.");
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: [],
        total: 0,
        loading: false,
        sort: null,
        direction: "asc",
        onSort: noop,
        onOpen: noop,
        emptyNote: line,
      }),
    );
    expect(html).toContain(line);
    expect(html).not.toContain("Nothing matches this view.");
    // A filter keeps the sentence of a filter.
    const page = read("../page.tsx");
    expect(page).toContain('ctx.mode === "mirror" && !isFiltered(view)');
  });
});

// ── Item 6: the panel ───────────────────────────────────────────────────────

describe("the panel (item 6)", () => {
  const html = renderToStaticMarkup(
    createElement(SuggestionPanelBody, { suggestion: SUGGESTION, listLabel: "Deals", preview: PREVIEW }),
  );

  it("shows three facts", () => {
    const three = facts(SUGGESTION, "Deals");
    expect(three).toEqual([
      "Commercials holds 24 records.",
      "2 columns come from its fields: Name and Probability.",
      "The stage comes from Status: 1 won, 1 lost and 2 open.",
    ]);
    for (const fact of three) expect(html).toContain(fact);
  });

  it("shows real records of the module, as the list would show them", () => {
    expect(html).toContain("Commercials 0");
    expect(html).toContain(">Won<");
    expect(html).toContain(">Not sorted<");
    expect(html).toContain("2 of 24 records, as Deals would show them");
  });

  it("shows at most three checks, from the counts of the records", () => {
    expect(checks(SUGGESTION, PREVIEW)).toEqual([
      "3 values of Probability did not fit the Probability column.",
      "1 record has a stage that is not sorted. It shows with no stage.",
    ]);
    const many: Suggestion = {
      ...SUGGESTION,
      targets: ["a", "b", "c", "d"].map((t) => ({ target: t, fields: [t], labels: [t], unmapped: 1 })),
    };
    expect(checks(many, null)).toHaveLength(3);
  });

  it("says what changes, and that nothing moves", () => {
    for (const line of whatChanges(SUGGESTION, "Deals")) expect(html).toContain(line);
    expect(html).toContain("No record moves");
  });

  it("has one action, which uses the module for the list", () => {
    expect(useLabel(SUGGESTION, "Deals")).toBe("Use Commercials as Deals");
    const panel = read("./Suggestions.tsx");
    expect(panel.match(/variant="primary"/g)?.length).toBe(1);
    expect(panel).toContain("{useLabel(suggestion, listLabel)}");
  });

  it("previews through the read-only preview route, with the list", async () => {
    const calls: Array<[string, string | undefined, unknown]> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init?.method, init?.body]);
      const text = JSON.stringify({ version: 7, entities: [], overlay: PREVIEW });
      return { ok: true, status: 200, json: async () => JSON.parse(text), text: async () => text };
    }) as typeof globalThis.fetch;
    try {
      expect(await api.previewSuggestion(7, "deal")).toEqual(PREVIEW);
      await api.approveSuggestion("deal", 7);
      await api.undoSuggestion("deal");
      await api.dismissSuggestion("deal", 7);
      await api.listSuggestions();
    } finally {
      globalThis.fetch = original;
    }
    expect(calls).toEqual([
      ["/api/crm/mapping/preview", "POST", JSON.stringify({ version: 7, slot: "deal" })],
      ["/api/crm/mapping/suggestions/deal/approve", "POST", JSON.stringify({ version: 7 })],
      ["/api/crm/mapping/suggestions/deal/undo", "POST", undefined],
      ["/api/crm/mapping/suggestions/deal/dismiss", "POST", JSON.stringify({ version: 7 })],
      ["/api/crm/mapping/suggestions", undefined, undefined],
    ]);
  });
});

// ── Item 7: Undo, in a toast and in Settings → Suggestions ─────────────────

describe("Undo (item 7)", () => {
  it("puts Undo in the toast of a use", () => {
    const panel = read("./Suggestions.tsx");
    expect(panel).toMatch(/title: usedLine\(s, label\),\s*action: \{ label: "Undo", onClick: \(\) => void undo\(s\) \}/);
    expect(usedLine(SUGGESTION, "Deals")).toBe("Deals now shows Commercials.");
    expect(undoneLine("Deals")).toBe("Deals shows its own list again.");
  });

  it("lists each list in use with Undo, and each suggestion with Preview and Dismiss", () => {
    const undone: string[] = [];
    const dismissed: string[] = [];
    const tree = SuggestionsCardView({
      list: LIST,
      handlers: handlers({
        onUndo: (o) => undone.push(o.slot),
        onDismiss: (s) => dismissed.push(s.slot),
      }),
    });
    buttonOf(tree, UNDO_LABEL)?.props.onClick?.();
    buttonOf(tree, DISMISS_LABEL)?.props.onClick?.();
    expect(undone).toEqual(["company"]);
    expect(dismissed).toEqual(["deal"]);
    const html = renderToStaticMarkup(createElement(SuggestionsCardView, { list: LIST, handlers: handlers() }));
    expect(html).toContain("Companies shows the records of Customers.");
    expect(html).toContain("Preview Commercials as Deals");
    const none = renderToStaticMarkup(
      createElement(SuggestionsCardView, { list: { suggestions: [], overlays: [] }, handlers: handlers() }),
    );
    expect(none).toContain(NO_SUGGESTIONS);
  });

  it("is one section of the one list, for an admin in Mirror mode only", () => {
    const ids = (ctx: Parameters<typeof visibleSections>[0]) => visibleSections(ctx).map((s) => s.id);
    expect(ids({ isAdmin: true, mode: "mirror" })).toContain("suggestions");
    expect(ids({ isAdmin: false, mode: "mirror" })).not.toContain("suggestions");
    expect(ids({ isAdmin: true, mode: "native", studioAdmin: true })).not.toContain("suggestions");
    const settings = read("./PipelineSettings.tsx");
    expect(settings).toContain('{readOnly && show("suggestions") && <SuggestionsCard />}');
  });
});

// ── Items 8 and 9: no engine word, no confidence number, no proposer ────────

describe("the words (item 8) and the proposer (item 9)", () => {
  it("shows no engine word and no confidence number on any surface", () => {
    const surfaces = [
      renderToStaticMarkup(
        createElement(SuggestionPanelBody, { suggestion: SUGGESTION, listLabel: "Deals", preview: PREVIEW }),
      ),
      renderToStaticMarkup(
        createElement(SuggestionPanelBody, { suggestion: SUGGESTION, listLabel: "Deals", preview: null }),
      ),
      renderToStaticMarkup(createElement(SuggestionsCardView, { list: LIST, handlers: handlers() })),
      renderToStaticMarkup(createElement(SuggestionsCardView, { list: null, handlers: handlers() })),
    ];
    for (const html of surfaces) expect(textOf(html)).not.toMatch(ENGINE_WORDS);
    const lines = [
      usedLine(SUGGESTION, "Deals"),
      undoneLine("Deals"),
      dismissedLine(SUGGESTION, "Deals"),
      memberEmptyLine("Deals"),
      ...checks(SUGGESTION, PREVIEW),
    ];
    for (const line of lines) expect(line).not.toMatch(ENGINE_WORDS);
  });

  it("would catch an engine word or a number of certainty", () => {
    expect("Approve draft v2").toMatch(ENGINE_WORDS);
    expect("62% sure").toMatch(ENGINE_WORDS);
    expect("Commercials plays the deal slot").toMatch(ENGINE_WORDS);
  });

  it("has no Suggest mappings button and no call of the old route", () => {
    expect(read("./FieldMapCard.tsx")).not.toMatch(/suggestFieldRules|SuggestControl|SUGGEST_LABEL|onSuggest/);
    expect(read("../lib/api.ts")).not.toContain("/field-rules/suggest");
    expect(read("../lib/fieldMap.ts")).not.toMatch(/confidenceLabel|% sure/);
  });
});

// ── Round 2 review P1: a write on an older draft ─────────────────────────────

describe("a suggestion never acts on an older draft (round 2 review P1)", () => {
  it("says in plain words that the map changed, on a 409 plan_moved", () => {
    const moved = new CrmError("The field map changed after this suggestion.", 409, "plan_moved");
    expect(suggestionError(moved, "x")).toBe(SUGGESTION_MOVED);
    expect(SUGGESTION_MOVED).not.toMatch(ENGINE_WORDS);
    const other = new CrmError("No module plays this list.", 404, "no_overlay");
    expect(suggestionError(other, "x")).toBe("No module plays this list.");
    expect(suggestionError(new Error("boom"), "That did not save.")).toBe("That did not save.");
  });

  it("reads the suggestions again after each write of the field map", () => {
    const card = read("./FieldMapCard.tsx");
    expect(card).toMatch(/function rereadPlan\(\): void \{\s*invalidate\(KEY_PREFIX\);\s*invalidate\(SUGGESTIONS_KEY\);/);
    expect(card).toContain("reread: rereadPlan,");
    // Each other read goes through `rereadPlan`, so no write skips the suggestions.
    expect(card.split("invalidate(KEY_PREFIX)").length - 1).toBe(1);
  });

  it("keeps the slot of the open panel, never a copy of the suggestion", () => {
    const panel = read("./Suggestions.tsx");
    expect(panel).not.toMatch(/useState<Suggestion \| null>/);
    expect(panel).toContain("list?.suggestions.find((s) => s.slot === previewingSlot)");
  });
});
