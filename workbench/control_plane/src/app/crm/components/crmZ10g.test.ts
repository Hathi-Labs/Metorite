// Record names (WS-53 CRM-Z10g, D133.1). An admin of a Native CRM renames
// the four record types, Mirror mode shows one line, and a member sees
// nothing. The runner has no DOM, so each view is rendered to a string, and a
// wire that only a file holds is read from its source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  BOTH_NAMES_PROBLEM,
  RECORD_NAME_MAX,
  RECORD_NAMES_MIRROR_NOTE,
  RECORD_NAMES_NOTE,
  RECORD_TYPES,
  SAVE_NAMES_LABEL,
  TOO_LONG_PROBLEM,
  USE_DEFAULT_LABEL,
  labelsBody,
  namesChanged,
  problemsOf,
  rowProblem,
  rowsOf,
  type NameRows,
} from "../lib/recordNames";
import { sourceSingular, viewLabel, type SourceLabels } from "../lib/views";
import { RecordNamesView, type RecordNamesViewProps } from "./RecordNamesCard";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const noop = () => undefined;
const textOf = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const SITES: SourceLabels = {
  contacts: { label: "Existing Sites", singular_label: "Existing Site" },
};

function view(props: Partial<RecordNamesViewProps>): string {
  return renderToStaticMarkup(
    createElement(RecordNamesView, {
      mode: "native",
      isAdmin: true,
      rows: rowsOf(null),
      changed: false,
      saving: false,
      onChange: noop,
      onSave: noop,
      ...props,
    }),
  );
}

describe("the card of a Native admin", () => {
  it("lists the four record types with the default names as placeholders", () => {
    const html = view({});
    const text = textOf(html);
    expect(text).toContain("Record names");
    expect(text).toContain(RECORD_NAMES_NOTE);
    expect(RECORD_TYPES.map((t) => t.slug)).toEqual(["leads", "contacts", "organizations", "deals"]);
    for (const type of RECORD_TYPES) {
      expect(html).toContain(`aria-label="${type.plural}"`);
      expect(html).toContain(`placeholder="${type.plural}"`);
      expect(html).toContain(`placeholder="${type.singular}"`);
    }
    expect(html).toContain('placeholder="Companies"');
    expect(html).toContain('placeholder="Company"');
    // Two boxes for each type, each capped, and one Save.
    expect(html.match(/<input/g)?.length).toBe(8);
    expect(html.match(new RegExp(`maxLength="${RECORD_NAME_MAX}"`, "g"))?.length).toBe(8);
    expect(html.match(new RegExp(SAVE_NAMES_LABEL, "g"))?.length).toBe(1);
  });

  it("shows the stored names in the boxes, with Use the default on that type only", () => {
    const html = view({ rows: rowsOf(SITES) });
    expect(html).toContain('value="Existing Sites"');
    expect(html).toContain('value="Existing Site"');
    expect(html.match(new RegExp(USE_DEFAULT_LABEL, "g"))?.length).toBe(1);
  });

  it("keeps Save off until a name changes, and off while a row is half filled", () => {
    const off = view({ changed: false });
    expect(off).toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
    const on = view({ rows: rowsOf(SITES), changed: true });
    expect(on).not.toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
    const half: NameRows = { ...rowsOf(null), contacts: { plural: "Existing Sites", singular: "" } };
    const blocked = view({ rows: half, changed: true });
    expect(textOf(blocked)).toContain(BOTH_NAMES_PROBLEM);
    expect(blocked).toContain('aria-invalid="true"');
    expect(blocked).toContain('aria-describedby="crm-record-name-contacts-problem"');
    expect(blocked).toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
  });
});

describe("Mirror mode, a member and no mode", () => {
  it("shows the Mirror note and no box", () => {
    const html = view({ mode: "mirror", rows: rowsOf(SITES), changed: true });
    expect(textOf(html)).toContain(RECORD_NAMES_MIRROR_NOTE);
    expect(RECORD_NAMES_MIRROR_NOTE).toBe(
      "Zoho names these. You can rename them after you switch to Metorite.",
    );
    expect(html).not.toContain("<input");
    expect(html).not.toContain(SAVE_NAMES_LABEL);
  });

  it("draws nothing for a member who is not an admin, in each mode", () => {
    for (const mode of ["native", "mirror", null] as const) {
      expect(view({ mode, isAdmin: false })).toBe("");
    }
  });

  it("draws nothing with no mode", () => {
    expect(view({ mode: null })).toBe("");
  });
});

describe("the rules of a name", () => {
  it("reads two empty boxes as the default, and one box alone as a problem", () => {
    expect(rowProblem({ plural: "", singular: "" })).toBeNull();
    expect(rowProblem({ plural: "  ", singular: "" })).toBeNull();
    expect(rowProblem({ plural: "Sites", singular: "" })).toBe(BOTH_NAMES_PROBLEM);
    expect(rowProblem({ plural: "", singular: "Site" })).toBe(BOTH_NAMES_PROBLEM);
    expect(rowProblem({ plural: "Sites", singular: "Site" })).toBeNull();
  });

  it("allows 60 characters after the trim and refuses 61", () => {
    const sixty = "S".repeat(RECORD_NAME_MAX);
    expect(rowProblem({ plural: ` ${sixty} `, singular: "Site" })).toBeNull();
    expect(rowProblem({ plural: `${sixty}S`, singular: "Site" })).toBe(TOO_LONG_PROBLEM);
  });

  it("sends each set row trimmed, and leaves an empty row out", () => {
    const rows: NameRows = {
      ...rowsOf(null),
      contacts: { plural: "  Existing Sites ", singular: " Existing Site" },
    };
    expect(labelsBody(rows)).toEqual(SITES);
    expect(labelsBody(rowsOf(null))).toEqual({});
    expect(problemsOf(rows)).toEqual({});
  });

  it("sees a change only when the trimmed names differ from the stored ones", () => {
    expect(namesChanged(rowsOf(SITES), SITES)).toBe(false);
    const spaced: NameRows = {
      ...rowsOf(SITES),
      contacts: { plural: "Existing Sites  ", singular: "Existing Site" },
    };
    expect(namesChanged(spaced, SITES)).toBe(false);
    expect(namesChanged(rowsOf(null), SITES)).toBe(true);
    expect(namesChanged(rowsOf(null), null)).toBe(false);
  });

  it("feeds the names the rail and the record page already read", () => {
    expect(viewLabel("contacts", SITES)).toBe("Existing Sites");
    expect(sourceSingular("contacts", SITES)).toBe("Existing Site");
    expect(viewLabel("deals", SITES)).toBe("Deals");
  });
});

describe("the wires", () => {
  it("mounts the card in the Fields section, for an admin", () => {
    const settings = read("./PipelineSettings.tsx");
    expect(settings).toContain('{show("fields") && <RecordNamesCard isAdmin={showAdminCards} />}');
  });

  it("writes through the one call, and the write wakes every CRM read", () => {
    const api = read("../lib/api.ts");
    const save = api.slice(api.indexOf("export function saveRecordNames"));
    expect(save).toMatch(/^[\s\S]*?writeAndWake\([\s\S]*?"\/settings\/labels"[\s\S]*?method: "PUT"/);
  });

  it("reads the stored names with their own key, and a failure stays a failure", () => {
    const hooks = read("../lib/useCrmMode.ts");
    const fn = hooks.slice(hooks.indexOf("export function useRecordNames"));
    // The function ends at the first closing brace at the start of a line.
    // A checkout on Windows can carry CRLF line ends.
    const body = fn.slice(0, fn.search(/\r?\n\}\r?\n/));
    expect(body.length).toBeGreaterThan(0);
    expect(body).toContain('"crm:record-names"');
    expect(body).not.toContain(".catch(");
    const card = read("./RecordNamesCard.tsx");
    expect(card).toContain("!names.known");
  });

  it("uses the shared controls, with no raw input", () => {
    const card = read("./RecordNamesCard.tsx");
    expect(card).not.toMatch(/<input\b/);
    expect(card).toContain('from "@/components/ui/Input"');
    expect(card).toContain('from "@/components/ui/Button"');
  });
});
