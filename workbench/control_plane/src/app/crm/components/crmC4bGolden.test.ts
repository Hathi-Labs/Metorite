// With the field studio off, the four C4b surfaces draw as the parent of C4b
// did, byte for byte (WS-53 CRM-C4b done-when 9, review round 1 item 4).
//
// The golden files in `__fixtures__/c4b-flag-off/` came from the parent
// commit 33b16848d, with this same file. To make them again, copy the parent
// tree of `src/app/crm` to a scratch folder under `src/app`, copy this file
// into its `components/`, and run it there with `C4B_GOLDEN_OUT` set to the
// fixture folder. Without `C4B_GOLDEN_OUT`, the file compares.
//
// One later edit by hand: `list.html` wraps the first cell in a button, so a
// keyboard opens the record (2026-10-16, `crmRowKeyboard.test.ts`). The flag
// changes nothing there, so both sides of the compare draw the button.
//
// A second later edit: `list.html` draws each cell on one line, with its
// full text in the title (WS-53 CRM-Q17a-2 item 3, 2026-10-16). The flag
// changes nothing there either. Only `list.html` changed: the other three
// files came out the same from this tree.
//
// The runner has no DOM. So `Modal` draws its children in a plain `div`, and
// for the list one `useState(false)` starts true, which opens the Columns
// chooser. Both sides of the compare run under the same two mocks.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import type { FieldDef } from "../lib/fields";
import { DEFAULT_VIEW } from "../lib/urlState";
import FieldsPanel from "./FieldsPanel";
import FilterBar from "./FilterBar";
import QuickCreateModal from "./QuickCreateModal";
import RecordList from "./RecordList";

const flags = vi.hoisted(() => ({ openFalse: false }));

vi.mock("react", async (importOriginal) => {
  const real = await importOriginal<typeof import("react")>();
  const useState = ((initial: unknown) =>
    real.useState(flags.openFalse && initial === false ? true : initial)) as typeof real.useState;
  return { ...real, default: { ...real, useState }, useState };
});

vi.mock("@/components/ui/Modal", () => ({
  default: (props: { title?: string; description?: string; children?: ReactNode }) =>
    createElement("div", { "data-modal": props.title }, props.description, props.children),
}));

const OUT = process.env.C4B_GOLDEN_OUT;
const GOLDEN = fileURLToPath(new URL("./__fixtures__/c4b-flag-off/", import.meta.url));
const noop = () => undefined;

const SECTION = { id: "11111111-1111-4111-8111-111111111111", label: "Sales facts", position: 0 };

const def = (key: string, type: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label: `My ${key}`,
  type,
  options:
    type === "select" || type === "multiselect"
      ? [
          { label: "Gold", value: "gold" },
          { label: "Silver", value: "silver" },
        ]
      : null,
  position: 0,
  visible: true,
  source: "native",
  api_name: null,
  ...over,
});

/** The custom fields, as the plain read gives them. */
const DEFS: FieldDef[] = [
  def("code", "text", { position: 1 }),
  def("tier", "select", { position: 2, section_id: SECTION.id }),
  def("seats", "number", { position: 3, section_id: SECTION.id }),
  def("signed", "boolean", { position: 4 }),
  def("starts", "date", { position: 5, section_id: SECTION.id }),
];

/** The layout with the flag off: sections and all, and `studio` false. */
const OFF = {
  entity: "deals",
  sections: [SECTION],
  fields: [def("name", "text", { label: "Deal name", is_system: true }), ...DEFS],
  studio: false,
  studio_admin: false,
};

const RECORD = {
  id: "d1",
  name: "Bosch order",
  amount: 5000,
  currency: "USD",
  custom: { code: "PO-1", tier: "gold", seats: 3 },
};

type Surface = "create" | "fields" | "list" | "filters";

function draw(surface: Surface, layout: typeof OFF | null): string {
  clearAll();
  if (layout) put("crm:field-layout:deals", layout);
  try {
    switch (surface) {
      case "create":
        return renderToStaticMarkup(
          createElement(QuickCreateModal, { entity: "deals", saving: false, onClose: noop, onCreate: noop }),
        );
      case "fields":
        return renderToStaticMarkup(
          createElement(FieldsPanel, {
            part: "fields",
            entity: "deals",
            record: RECORD,
            statuses: [],
            dealContacts: [],
            saving: false,
            onPatch: noop,
            onConvert: noop,
            onSetPrimary: noop,
            onDetach: noop,
            onOpenContact: noop,
            fieldDefs: DEFS,
            ...(layout ? { layout } : {}),
          }),
        );
      case "list":
        flags.openFalse = true;
        return renderToStaticMarkup(
          createElement(RecordList, {
            entity: "deals",
            rows: [{ id: "1", name: "Bosch order", amount: 5000, currency: "USD", custom: { tier: "gold" } }],
            total: 1,
            loading: false,
            sort: null,
            direction: "asc",
            onSort: noop,
            onOpen: noop,
            fieldDefs: DEFS,
            columnKeys: ["tier"],
          }),
        );
      case "filters":
        return renderToStaticMarkup(
          createElement(FilterBar, {
            entity: "deals",
            view: { ...DEFAULT_VIEW, tab: "deals", custom: { tier: ["gold"] } },
            onChange: noop,
            fieldDefs: DEFS,
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
    }
  } finally {
    flags.openFalse = false;
    clearAll();
  }
}

const SURFACES: Surface[] = ["create", "fields", "list", "filters"];

afterEach(() => clearAll());

if (OUT) {
  describe("write the golden files from this tree", () => {
    it("writes one file for each surface", () => {
      if (!existsSync(OUT)) mkdirSync(OUT, { recursive: true });
      for (const surface of SURFACES) writeFileSync(join(OUT, `${surface}.html`), draw(surface, null));
    });
  });
} else {
  describe("with studio false or no layout, each surface draws as the parent did (done-when 9)", () => {
    for (const surface of SURFACES) {
      it(`draws ${surface} as the golden file`, () => {
        const golden = readFileSync(join(GOLDEN, `${surface}.html`), "utf8");
        expect(golden.length).toBeGreaterThan(200);
        expect(draw(surface, OFF)).toBe(golden);
        expect(draw(surface, null)).toBe(golden);
      });
    }

    it("opens the Columns chooser in the list golden file", () => {
      const golden = readFileSync(join(GOLDEN, "list.html"), "utf8");
      expect(golden).toContain('data-modal="Columns"');
      expect(golden).toContain("My seats");
    });
  });
}
