// Record names (WS-53 CRM-Z10g, D133.1). An admin of a Native CRM renames
// the four record types, Mirror mode shows one line, and a member sees
// nothing. The runner has no DOM, so each view is rendered to a string, and a
// wire that only a file holds is read from its source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  LIST_NAME_PROBLEM,
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
import { clearAll, put } from "@/lib/dataCache";

import { effectiveMode } from "../lib/connect";
import { useSourceLabels } from "../lib/useCrmMode";
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

  it("keeps Save off until a name changes, and off with a record name and no list name", () => {
    const off = view({ changed: false });
    expect(off).toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
    const on = view({ rows: rowsOf(SITES), changed: true });
    expect(on).not.toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
    const half: NameRows = { ...rowsOf(null), contacts: { plural: "", singular: "Existing Site" } };
    const blocked = view({ rows: half, changed: true });
    expect(textOf(blocked)).toContain(LIST_NAME_PROBLEM);
    expect(blocked).toContain('aria-invalid="true"');
    expect(blocked).toContain('aria-describedby="crm-record-name-contacts-problem"');
    expect(blocked).toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
  });

  it("loads a stored list name with no singular name, with no problem, and saves it (verifier F2)", () => {
    // The server stores this shape, and CRM-Z10b stores it when the Zoho
    // profile has no singular name. The card used to show "Fill in both
    // names" on load and block Save.
    const stored: SourceLabels = { contacts: { label: "Existing Sites", singular_label: null } };
    const rows = rowsOf(stored);
    expect(rows.contacts).toEqual({ plural: "Existing Sites", singular: "" });
    expect(problemsOf(rows)).toEqual({});
    expect(namesChanged(rows, stored)).toBe(false);

    const html = view({ rows, changed: true });
    expect(textOf(html)).not.toContain(LIST_NAME_PROBLEM);
    expect(html).not.toContain('aria-invalid="true"');
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?Save names/);
    // The empty box says what the record page shows: the list name.
    expect(html).toContain('placeholder="Existing Sites"');
    expect(html).not.toContain('placeholder="Contact"');

    // The body sends `null`, never "": the server refuses an empty name.
    expect(labelsBody(rows)).toEqual(stored);
    // The record page reads the plural.
    expect(sourceSingular("contacts", stored)).toBe("Existing Sites");
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
    // Verifier F3: the note is a body paragraph after the header, so the
    // header's bottom border does not sit on the border of the card.
    const header = html.slice(html.indexOf("<header"), html.indexOf("</header>"));
    expect(header).not.toContain(RECORD_NAMES_MIRROR_NOTE);
    expect(html).toMatch(
      new RegExp(
        `</header><p class="px-4 py-3 text-xs text-muted-foreground">${RECORD_NAMES_MIRROR_NOTE}</p></section>`,
      ),
    );
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
  it("reads two empty boxes as the default, a list name alone as enough, and a record name alone as a problem", () => {
    expect(rowProblem({ plural: "", singular: "" })).toBeNull();
    expect(rowProblem({ plural: "  ", singular: "" })).toBeNull();
    expect(rowProblem({ plural: "Sites", singular: "" })).toBeNull();
    expect(rowProblem({ plural: "Sites", singular: "  " })).toBeNull();
    expect(rowProblem({ plural: "", singular: "Site" })).toBe(LIST_NAME_PROBLEM);
    expect(rowProblem({ plural: " ", singular: "Site" })).toBe(LIST_NAME_PROBLEM);
    expect(rowProblem({ plural: "Sites", singular: "Site" })).toBeNull();
  });

  it("sends a list name alone with a null singular name", () => {
    const rows: NameRows = { ...rowsOf(null), deals: { plural: " Orders ", singular: "  " } };
    expect(labelsBody(rows)).toEqual({ deals: { label: "Orders", singular_label: null } });
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

describe("a member reads the names (review P1)", () => {
  /** Draws the plural name of Contacts that `useSourceLabels(enabled)` gives. */
  function Probe({ enabled }: { enabled: boolean }) {
    return createElement("span", null, useSourceLabels(enabled)?.contacts?.label ?? "none");
  }
  const probe = (enabled: boolean) =>
    textOf(renderToStaticMarkup(createElement(Probe, { enabled }))).trim();

  it("reads the names when the client mode is null: connection null, settings mode undefined", () => {
    // A member of a Native org off CRM_MIRROR_ORGS: `GET /crm/connection` is
    // 404 (connection null), and `GET /crm/settings` is 403 (the admin floor),
    // so `readSettings` gives null and the settings mode is undefined.
    const mode = effectiveMode(null, undefined);
    expect(mode).toBeNull();

    // The page reads the names with no gate on that mode.
    const page = read("../page.tsx");
    expect(/useSourceLabels\(([^)]*)\)/.exec(page)?.[1]).toBe("true");

    // The server serves the stored names to a member by its own mode. The
    // cache holds that answer, so the first render draws it with no DOM.
    clearAll();
    try {
      put("crm:mode", { mode: "native", labels: SITES });
      expect(probe(true)).toBe("Existing Sites");
      // The old gate, `ctx.mode !== null`, read nothing for this member.
      expect(probe(mode !== null)).toBe("none");
    } finally {
      clearAll();
    }
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
