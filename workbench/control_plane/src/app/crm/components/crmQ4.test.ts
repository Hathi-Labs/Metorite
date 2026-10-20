// Field definitions in the CRM UI (WS-53 CRM-Q4, spec section 18.3 and 18.5).
// The runner has no DOM, so each component is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  MAX_COLUMNS,
  chosenDefs,
  columnsKey,
  fieldRows,
  formatField,
  listCell,
  pickable,
  readColumns,
  safeHref,
  toggleColumn,
  writeColumns,
  type FieldDef,
} from "../lib/fields";
import FieldValue from "./FieldValue";
import FieldsCard, { FieldsCardView } from "./FieldsCard";
import FieldsPanel from "./FieldsPanel";
import type { ViewActions } from "../lib/useViewActions";
import CrmViewBar from "./CrmViewBar";
import RecordList from "./RecordList";

const noop = () => undefined;

const def = (key: string, type: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label: key.toUpperCase(),
  type,
  options: null,
  position: 0,
  visible: true,
  source: "zoho",
  api_name: key,
  ...over,
});

const draw = (field: FieldDef | null, value: unknown) =>
  renderToStaticMarkup(createElement(FieldValue, { value: formatField(field, value) }));

// ── Rendering by type ─────────────────────────────────────────────────────

describe("formatting a custom field by its type", () => {
  it("formats a date with the app's helper", () => {
    expect(formatField(def("d", "date"), "2020-03-05")).toEqual({ kind: "text", text: "5 Mar 2020" });
  });

  it("groups a number and a currency the Indian way", () => {
    expect(formatField(def("n", "number"), 1234567)).toEqual({ kind: "text", text: "12,34,567" });
    const money = formatField(def("m", "currency"), "120000");
    expect(money.kind === "text" && money.text).toContain("1,20,000");
  });

  it("reads a boolean as Yes or No", () => {
    expect(formatField(def("b", "boolean"), true)).toEqual({ kind: "text", text: "Yes" });
    expect(formatField(def("b", "boolean"), false)).toEqual({ kind: "text", text: "No" });
  });

  it("shows a picklist value as it is", () => {
    expect(formatField(def("s", "select"), "Gold")).toEqual({ kind: "text", text: "Gold" });
  });

  it("draws a multi-select as chips", () => {
    const html = draw(def("m", "multiselect"), ["Red", "Blue"]);
    expect(html).toContain("Red");
    expect(html).toContain("Blue");
    expect(html.match(/rounded-full/g)).toHaveLength(2);
  });

  it("draws an email as a mailto link", () => {
    const html = draw(def("e", "email"), "a@b.in");
    expect(html).toContain('href="mailto:a@b.in"');
  });

  it("never builds a mailto from a value that carries a query", () => {
    for (const bad of ["a@b.com?bcc=x&subject=y", "a@b.com#x", "a@b.com%0Abcc:x", "a@b.com&x=1", "a@b.com y"]) {
      expect(formatField(def("e", "email"), bad).kind).toBe("text");
      expect(draw(def("e", "email"), bad)).not.toContain("mailto:");
    }
  });

  it("caps the chips: 20 on the record page, 3 in a list cell, then a +N chip", () => {
    const items = Array.from({ length: 10000 }, (_, i) => `v${i}`);
    const value = formatField(def("m", "multiselect"), items);
    const record = renderToStaticMarkup(createElement(FieldValue, { value }));
    expect(record.match(/rounded-full/g)).toHaveLength(21);
    expect(record).toContain("+9980");
    const cell = renderToStaticMarkup(createElement(FieldValue, { value, compact: true }));
    expect(cell.match(/rounded-full/g)).toHaveLength(4);
    expect(cell).toContain("+9997");
  });

  it("reads the display name of a lookup", () => {
    expect(formatField(def("l", "lookup"), { name: "Bosch", id: "9" })).toEqual({ kind: "text", text: "Bosch" });
  });

  it("wraps long text", () => {
    expect(draw(def("t", "textarea"), "one\ntwo")).toContain("whitespace-pre-wrap");
  });

  it("shows an em dash for an empty value", () => {
    expect(draw(def("t", "text"), null)).toBe("—");
    expect(draw(def("t", "multiselect"), [])).toBe("—");
  });
});

describe("the URL guard", () => {
  it("links an http or https address with rel noopener noreferrer", () => {
    const html = draw(def("u", "url"), "https://example.com/a");
    expect(html).toContain('href="https://example.com/a"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("never links any other scheme", () => {
    for (const bad of ["javascript:alert(1)", "data:text/html,x", "ftp://x.in", "//evil.in", "https://a b"]) {
      expect(safeHref(bad)).toBeNull();
      expect(draw(def("u", "url"), bad)).not.toContain("<a");
    }
  });
});

// ── The record page ───────────────────────────────────────────────────────

describe("the custom rows of a record", () => {
  const record = { custom: { b_field: "x", a_field: "y", hidden_one: "z", loose_key: "w" } };
  const defs = [
    def("a_field", "text", { label: "Alpha", position: 2 }),
    def("b_field", "text", { label: "Bravo", position: 1 }),
    def("hidden_one", "text", { label: "Secret", visible: false }),
  ];

  it("orders by position and uses the label", () => {
    const rows = fieldRows(record, defs);
    expect(rows.map((r) => r.label).slice(0, 2)).toEqual(["Bravo", "Alpha"]);
  });

  it("drops a hidden field", () => {
    expect(fieldRows(record, defs).map((r) => r.key)).not.toContain("hidden_one");
  });

  it("keeps a key with no definition, under its humanised key, after the defined ones", () => {
    const rows = fieldRows(record, defs);
    expect(rows[rows.length - 1]).toMatchObject({ key: "loose_key", label: "Loose key" });
  });

  it("falls back to the humanised keys without definitions", () => {
    for (const none of [null, undefined, []]) {
      expect(fieldRows(record, none).map((r) => r.label)).toEqual([
        "B field",
        "A field",
        "Hidden one",
        "Loose key",
      ]);
    }
  });

  it("draws the Fields tab from the definitions, and read only in Mirror mode", () => {
    const html = renderToStaticMarkup(
      createElement(FieldsPanel, {
        part: "fields",
        entity: "deals",
        record: { id: "d1", name: "Deal", ext_source: "zoho", custom: { tier: "Gold", site: "https://x.in" } },
        statuses: [],
        dealContacts: [],
        saving: false,
        onPatch: noop,
        onConvert: noop,
        onSetPrimary: noop,
        onDetach: noop,
        onOpenContact: noop,
        fieldDefs: [def("tier", "select", { label: "Tier" }), def("site", "url", { label: "Site", position: 1 })],
      } as never),
    );
    expect(html).toContain("Tier");
    expect(html).toContain('href="https://x.in"');
    expect(html).not.toContain("<input");
  });
});

// ── The Fields card ───────────────────────────────────────────────────────

describe("the Fields card", () => {
  const byEntity = {
    leads: [def("tier", "select", { label: "Tier", options: [{ label: "Gold", value: "gold" }] })],
    contacts: [def("old", "text", { label: "Old field", visible: false, source: "native" })],
    organizations: [],
    deals: null,
  };

  it("lists label, type, source, the hidden flag and the options in Mirror mode", () => {
    const html = renderToStaticMarkup(createElement(FieldsCardView, { byEntity, readOnly: true }));
    expect(html).toContain("Fields come from Zoho.");
    expect(html).not.toContain("Only a studio admin can edit fields.");
    expect(html).toContain("Tier");
    expect(html).toContain("select");
    expect(html).toContain("Zoho");
    expect(html).toContain("Gold");
    expect(html).toContain("hidden");
    expect(html).toContain("Metorite");
  });

  it("lists the same in Native mode and says that only a studio admin edits", () => {
    const html = renderToStaticMarkup(createElement(FieldsCardView, { byEntity, readOnly: false }));
    expect(html).toContain("Only a studio admin can edit fields.");
    expect(html).not.toContain("Fields come from Zoho.");
    expect(html).toContain("Tier");
    expect(html).not.toContain("<input");
  });

  it("names the four entities, and an empty one says so", () => {
    const html = renderToStaticMarkup(createElement(FieldsCardView, { byEntity, readOnly: false }));
    for (const name of ["Leads", "Contacts", "Companies", "Deals"]) expect(html).toContain(name);
    expect(html).toContain("No custom fields yet.");
  });

  it("mounts with its own reads", () => {
    const html = renderToStaticMarkup(createElement(FieldsCard, { readOnly: false }));
    expect(html).toContain("Fields");
  });
});

// ── The column chooser ────────────────────────────────────────────────────

function memoryStore(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
  };
}

describe("the column choice", () => {
  it("is stored for each entity, and read back", () => {
    const store = memoryStore();
    writeColumns("deals", ["a", "b"], store);
    writeColumns("leads", ["c"], store);
    expect(readColumns("deals", store)).toEqual(["a", "b"]);
    expect(readColumns("leads", store)).toEqual(["c"]);
    // CRM-Q17a-2: nothing saved is null, and a saved empty list stays [].
    expect(readColumns("contacts", store)).toBeNull();
    writeColumns("organizations", [], store);
    expect(readColumns("organizations", store)).toEqual([]);
    expect(Object.keys(store.data)).toContain(columnsKey("deals"));
  });

  it("holds at most five", () => {
    let chosen: string[] = [];
    for (const k of ["a", "b", "c", "d", "e", "f"]) chosen = toggleColumn(chosen, k);
    expect(chosen).toHaveLength(MAX_COLUMNS);
    expect(chosen).not.toContain("f");
    expect(toggleColumn(chosen, "a")).toEqual(["b", "c", "d", "e"]);
    const store = memoryStore();
    writeColumns("deals", ["a", "b", "c", "d", "e", "f", "g"], store);
    expect(readColumns("deals", store)).toHaveLength(MAX_COLUMNS);
  });

  it("never throws when the store is broken or blocked", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    // Each of these reads as nothing saved (CRM-Q17a-2 item 2).
    expect(readColumns("deals", broken)).toBeNull();
    expect(() => writeColumns("deals", ["a"], broken)).not.toThrow();
    expect(readColumns("deals", memoryStore({ [columnsKey("deals")]: "{not json" }))).toBeNull();
    expect(readColumns("deals", memoryStore({ [columnsKey("deals")]: '{"a":1}' }))).toBeNull();
    expect(readColumns("deals", null)).toBeNull();
  });

  it("drops a choice that left the definitions or went hidden", () => {
    const defs = [def("a", "text"), def("b", "text", { visible: false })];
    expect(chosenDefs(["a", "b", "gone"], defs).map((d) => d.key)).toEqual(["a"]);
    expect(chosenDefs(["a"], null)).toEqual([]);
  });

  it("formats a list cell by the same type rules", () => {
    expect(listCell({ custom: { flag: true } }, def("flag", "boolean"))).toEqual({ kind: "text", text: "Yes" });
    expect(listCell({ custom: null }, def("flag", "boolean"))).toEqual({ kind: "empty" });
  });
});

describe("the list", () => {
  const props = {
    entity: "deals" as const,
    rows: [{ id: "1", name: "Deal one", custom: { tier: "Gold", hid: "no" } }],
    total: 1,
    loading: false,
    sort: null,
    direction: "asc" as const,
    onSort: noop,
    onOpen: noop,
  };
  const defs = [def("tier", "select", { label: "Tier" }), def("hid", "text", { label: "Hid", visible: false })];

  it("offers the chooser button in row 1 when fields exist, and none without", () => {
    // CRM-U8: the Columns band left the list. The toolbar owns the button, and
    // the page hides it when no field can be a column.
    const actions = {
      views: [], active: null, dirty: "blank", busy: false, columns: null,
      setColumns: noop, select: noop, save: async () => undefined,
      update: async () => undefined, reset: noop, rename: async () => undefined,
      toggleShare: async () => undefined, remove: async () => undefined,
      deleteCopy: () => ({ title: "", body: "" }),
    } as unknown as ViewActions;
    const bar = (onColumns: (() => void) | null) =>
      renderToStaticMarkup(createElement(CrmViewBar, { allLabel: "All deals", actions, isAdmin: false, onColumns }));
    expect(bar(noop)).toContain("Columns");
    expect(bar(null)).not.toContain("Columns");
    expect(renderToStaticMarkup(createElement(RecordList, { ...props, fieldDefs: defs }))).not.toContain("Columns");
  });

  it("shows no custom column until the member picks one", () => {
    const html = renderToStaticMarkup(createElement(RecordList, { ...props, fieldDefs: defs }));
    expect(html).not.toContain("Tier");
  });
});

// ── WS-53 CRM-C4a: the reader filter (spec section 20.7 items 1 and 11) ────

describe("the field studio rows in the readers", () => {
  it("never reads a system row as the definition of a custom key", () => {
    const rows = fieldRows({ custom: { region: "North" } }, [
      def("region", "text", { label: "Area", is_system: true }),
    ]);
    expect(rows.map((r) => r.label)).toEqual(["Region"]);
  });

  it("hides a retired field on the record, also when it is still visible", () => {
    // A retire keeps `visible` (review round 1), so the flag must hide it.
    const rows = fieldRows({ custom: { old: "x", live: "y" } }, [
      def("old", "text", { retired: true, visible: true }),
      def("live", "text", { label: "Live" }),
    ]);
    expect(rows.map((r) => r.label)).toEqual(["Live"]);
  });

  it("offers no retired field and no system row in the column picker", () => {
    const picked = pickable([
      def("tier", "select", { position: 2 }),
      def("old", "text", { position: 1, retired: true }),
      def("name", "text", { position: 0, is_system: true }),
    ]);
    expect(picked.map((d) => d.key)).toEqual(["tier"]);
  });

  it("draws the two new number words", () => {
    expect(formatField(def("d", "decimal"), 1234.5)).toEqual({ kind: "text", text: "1,234.5" });
    expect(formatField(def("p", "percent"), 12.5)).toEqual({ kind: "text", text: "12.5%" });
  });
});
