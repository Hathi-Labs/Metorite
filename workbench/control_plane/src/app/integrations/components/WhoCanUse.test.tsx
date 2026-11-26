// ACC-2 (task 3988): "Who can use", drawn. The runner has no DOM, so each
// part is rendered to a string. The dialog itself is a portal, so its body is
// what renders here, and the visual review opens the dialog in a browser.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { row } from "../lib/testRows";
import type { Connection } from "../lib/connections";
import {
  EMPTY_CHOICE_WARNING,
  UNREADABLE_ALL,
  rowRule,
  type AccessItem,
  type AccessList,
} from "../lib/whoCanUse";
import ConnectionList from "./ConnectionList";
import {
  AccessNotice,
  UnmatchedRules,
  WhoCanUseBody,
  WhoCanUseRow,
  type PickList,
  type WhoCanUseBodyProps,
} from "./WhoCanUse";

const noop = () => undefined;

const item = (over: Partial<AccessItem> = {}): AccessItem => ({
  service: "mcp-tally",
  kind: "mcp",
  mode: "everyone",
  groups: [],
  people: [],
  set_by: "",
  set_at: "",
  listed: true,
  readable: true,
  ...over,
});

const rows: Connection[] = [
  row({
    id: "mcp:tally",
    kind: "mcp",
    provider: "mcp",
    label: "Tally",
    owning_app: "integrations",
    detail: { name: "tally" },
  }),
  row({ id: "zoho:1", label: "Zoho CRM" }),
];

/** The list as `ConnectionsView` draws it for one answer of the access read. */
const listHtml = (access: AccessList | null | undefined) =>
  renderToStaticMarkup(
    createElement(ConnectionList, {
      rows,
      total: rows.length,
      loading: false,
      onOpen: noop,
      rowExtra: (r: Connection) => {
        const found = rowRule(access, r);
        return found
          ? createElement(WhoCanUseRow, { item: found, listReadable: true, label: r.label, onOpen: noop })
          : null;
      },
    }),
  );

describe("the control on a row", () => {
  it("says Everyone for a row with no rule, on the MCP row only", () => {
    const html = listHtml({ services: [item()], readable: true });
    expect(html).toContain("Who can use: Everyone");
    expect(html.match(/data-who-can-use=/g)).toHaveLength(1);
    expect(html).toContain('data-who-can-use="mcp-tally"');
    expect(html).toContain('aria-label="Who can use: Everyone (Tally)"');
  });

  it("draws no control and no error off the flag, with no right, or while it loads", () => {
    for (const access of [null, undefined]) {
      const html = listHtml(access);
      expect(html).toContain("Tally");
      expect(html).not.toContain("Who can use");
      expect(html).not.toContain('role="alert"');
    }
  });

  it("keeps the control outside the button that opens the row", () => {
    const html = listHtml({ services: [item()], readable: true });
    // A button inside a button is invalid HTML, so each opens and closes alone.
    expect(html).not.toMatch(/<button(?:(?!<\/button>).)*<button/);
  });

  it("is a plain fact, with no control, while the stored value cannot be read", () => {
    const html = renderToStaticMarkup(
      createElement(WhoCanUseRow, {
        item: item({ mode: "admins", readable: false }),
        listReadable: false,
        label: "Tally",
        onOpen: noop,
      }),
    );
    expect(html).toContain("Who can use: Admins only");
    expect(html).not.toContain("<button");
  });
});

const notice = (props: Partial<Parameters<typeof AccessNotice>[0]>) =>
  renderToStaticMarkup(
    createElement(AccessNotice, { list: undefined, error: null, onRetry: noop, ...props }),
  );

describe("the notice above the list", () => {
  it("draws nothing off the flag, while it loads, and for a good answer", () => {
    expect(notice({ list: null })).toBe("");
    expect(notice({ list: undefined })).toBe("");
    expect(notice({ list: { services: [], readable: true } })).toBe("");
  });

  it("says that the stored value cannot be read, and who can use things now", () => {
    const html = notice({ list: { services: [], readable: false } });
    expect(html).toContain(UNREADABLE_ALL);
    expect(html).toContain('role="alert"');
  });

  it("says that the read failed, with Try again", () => {
    const html = notice({ error: "Gateway unreachable." });
    expect(html).toContain("Could not read who can use each integration. Gateway unreachable.");
    expect(html).toContain("Try again");
  });
});

describe("rules with no connection", () => {
  const draw = (items: AccessItem[]) =>
    renderToStaticMarkup(createElement(UnmatchedRules, { items, onChange: noop, onClear: noop }));

  it("offers only Everyone for a service that left the list", () => {
    const html = draw([item({ service: "mcp-old", kind: "", mode: "admins", listed: false })]);
    expect(html).toContain("mcp-old");
    expect(html).toContain("Who can use: Admins only");
    expect(html).toContain("Set to Everyone");
    expect(html).not.toContain(">Change<");
  });

  it("offers Change for a listed service that has no connection", () => {
    const html = draw([
      item({ service: "api-zoho_books", kind: "api", mode: "chosen", people: ["a@x.test"] }),
    ]);
    expect(html).toContain("Who can use: 1 person");
    expect(html).toContain("Change who can use api-zoho_books");
    expect(html).not.toContain("Set to Everyone:");
  });

  it("draws nothing when there is none", () => {
    expect(draw([])).toBe("");
  });

  it("shows under an empty list too", () => {
    const html = renderToStaticMarkup(
      createElement(ConnectionList, {
        rows: [],
        total: 0,
        loading: false,
        onOpen: noop,
        footer: createElement(UnmatchedRules, {
          items: [item({ service: "mcp-old", mode: "admins", listed: false })],
          onChange: noop,
          onClear: noop,
        }),
      }),
    );
    expect(html).toContain("No connections yet.");
    expect(html).toContain("Other stored rules");
  });
});

const picks = (over: Partial<PickList> = {}): PickList => ({
  options: [],
  loading: false,
  error: null,
  onRetry: noop,
  ...over,
});

const body = (over: Partial<WhoCanUseBodyProps> = {}) =>
  renderToStaticMarkup(
    createElement(WhoCanUseBody, {
      item: item(),
      mode: "everyone",
      onMode: noop,
      groups: [],
      people: [],
      onGroup: noop,
      onPerson: noop,
      teams: picks({ options: [{ value: "sales", label: "Sales", hint: "3 members" }] }),
      members: picks({ options: [{ value: "a@x.test", label: "Asha", hint: "a@x.test" }] }),
      query: "",
      onQuery: noop,
      saving: false,
      error: null,
      onSave: noop,
      onCancel: noop,
      ...over,
    }),
  );

/** The opening tag of the Save button. */
const saveButton = (html: string) =>
  html.match(/<button[^>]*>(?:(?!<\/button>).)*(?:Save|Save as admins only)<\/button>/)?.[0] ?? "";

describe("the open control", () => {
  it("offers the three modes as one radio group", () => {
    const html = body();
    expect(html).toContain('role="radiogroup"');
    expect(html.match(/role="radio"/g)).toHaveLength(3);
    for (const word of ["Everyone", "Chosen teams and people", "Admins only"]) expect(html).toContain(word);
    expect(html.match(/aria-checked="true"/g)).toHaveLength(1);
    // Everyone and Admins only show no picker.
    expect(html).not.toContain("<fieldset");
    expect(body({ mode: "admins" })).not.toContain("<fieldset");
  });

  it("shows the team picker and the people picker for chosen", () => {
    const html = body({ mode: "chosen", groups: ["sales"] });
    for (const word of ["Teams", "People", "Sales", "3 members", "Asha", "1 picked", "Search by name or email"]) {
      expect(html).toContain(word);
    }
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain(EMPTY_CHOICE_WARNING);
  });

  it("says that chosen with nothing picked means admins only, before the save", () => {
    const html = body({ mode: "chosen" });
    expect(html).toContain(EMPTY_CHOICE_WARNING);
    expect(html).toContain("Save as admins only");
  });

  it("disables Save when nothing changed, and enables it after a change", () => {
    expect(saveButton(body())).toContain('disabled=""');
    expect(saveButton(body({ mode: "admins" }))).not.toContain('disabled=""');
  });

  it("shows the gateway's words of a refused save", () => {
    const html = body({ mode: "chosen", groups: ["gone"], error: "Unknown group(s): ['gone']." });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Unknown group(s): [&#x27;gone&#x27;].");
  });

  it("says when the stored rule cannot be read", () => {
    const html = body({ item: item({ mode: "admins", readable: false }), mode: "admins" });
    expect(html).toContain("cannot read the stored rule");
    expect(saveButton(html)).not.toContain('disabled=""');
  });

  it("shows the loading, empty, error and no-match states of the pickers", () => {
    expect(body({ mode: "chosen", teams: picks({ loading: true }) })).toContain('aria-label="Loading teams"');
    expect(body({ mode: "chosen", teams: picks() })).toContain("No teams yet.");
    const failed = body({ mode: "chosen", members: picks({ error: "Could not read the people." }) });
    expect(failed.match(/Could not read the people\./g)).toHaveLength(1);
    expect(failed).toContain("Try again");
    // The server's words keep the name of what failed in front.
    expect(body({ mode: "chosen", teams: picks({ error: "Gateway unreachable." }) })).toContain(
      "Could not read the teams. Gateway unreachable.",
    );
    expect(failed).not.toContain("No members yet.");
    expect(body({ mode: "chosen", query: "zed" })).toContain("No match for “zed”.");
  });
});
