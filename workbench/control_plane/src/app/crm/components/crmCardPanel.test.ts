/**
 * The CRM record card draws the shared frame (ORC-1, D181,
 * `specs/one_record_card.md` §6).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type ComponentType, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TaskHeaderRow } from "@/components/TaskPanelHeader";

import RecordList from "./RecordList";

const read = (file: string) => readFileSync(join(__dirname, file), "utf-8");
/** The code of a file, with its comments gone, so a note cannot pass a fence. */
const code = (file: string) =>
  read(file)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

describe("one frame for each record kind", () => {
  it("the shell draws CardPanel, and no Modal", () => {
    const shell = code("./RecordShell.tsx");
    expect(shell).toContain('from "@/components/CardPanel"');
    expect(shell).toContain("<CardPanel");
    expect(shell).not.toContain("ui/Modal");
    expect(shell).not.toContain("<Modal");
  });

  it("both record sheets draw the shell's card", () => {
    for (const sheet of ["./RecordSheet.tsx", "./ModuleRecordSheet.tsx"]) {
      expect(code(sheet), sheet).toContain("<RecordCard");
    }
  });

  it("the page mounts each card once, in the row beside main", () => {
    const page = code("../page.tsx");
    const mainEnd = page.indexOf("</main>");
    expect(mainEnd).toBeGreaterThan(0);
    for (const tag of ["<RecordSheet", "<ModuleRecordSheet"]) {
      expect(page.indexOf(tag), tag).toBeGreaterThan(mainEnd);
      expect(page.indexOf(tag), tag).toBe(page.lastIndexOf(tag));
    }
    // The row closes after both, so the docked stop is the row's own column.
    const rowEnd = page.indexOf("</div>", page.indexOf("<ModuleRecordSheet"));
    expect(rowEnd).toBeGreaterThan(page.indexOf("<ModuleRecordSheet"));
  });

  it("the page root says that a key on the board belongs to the card", () => {
    expect(code("../page.tsx")).toContain("[CARD_PAGE_ATTR]");
  });

  it("a click on the list with a card open replaces the URL, so Back closes it", () => {
    const page = code("../page.tsx");
    expect(page).toContain("cardOpen ? replaceView : pushView");
    expect(page).toContain("openInCard(openRecord(view, param, id))");
    expect(page).toContain("openInCard(openRecord(view, PARAM_FOR[listEntity], id))");
    expect(page).toContain("openInCard(openModuleRecord(view, view.module!, id))");
  });
});

describe("a record that loads or fails", () => {
  it("both sheets draw the one failure state", () => {
    expect(code("./RecordSheet.tsx")).toContain("<RecordLoadFailed");
    expect(code("./ModuleRecordSheet.tsx")).toContain("<RecordLoadFailed");
  });

  it("the steps take the id from the URL, so they stay while the record loads", () => {
    const sheet = code("./RecordSheet.tsx");
    expect(sheet).toContain("<RecordSteps order={order} id={id}");
    expect(code("../page.tsx")).toContain("id={view.record.id}");
  });
});

describe("the header is the Projects header", () => {
  it("the shell draws TaskHeaderRow with the noun of a record", () => {
    const shell = code("./RecordShell.tsx");
    expect(shell).toContain("<TaskHeaderRow");
    expect(shell).toContain('noun="record"');
  });

  it("the words say record, in the Projects order", () => {
    const html = renderToStaticMarkup(
      createElement(TaskHeaderRow, {
        taskRef: "Deal",
        noun: "record",
        linkFor: () => "https://app.metorite.com/crm?deal=1",
        expand: { expanded: false, onToggle: () => undefined },
        onClose: () => undefined,
      }),
    );
    expect(html).toContain("Deal");
    expect(html).toContain('aria-label="Copy a link to this record"');
    expect(html).toContain('aria-label="Close record"');
    expect(html).not.toContain("this task");
    expect(html).not.toContain("Close task");
    const copy = html.indexOf("Copy a link");
    const expand = html.indexOf("Open as a full card");
    const close = html.indexOf("Close record");
    expect(copy).toBeLessThan(expand);
    expect(expand).toBeLessThan(close);
  });

  it("the toggle comes from the shared stop, which holds none for a phone", () => {
    const shell = code("./RecordShell.tsx");
    expect(shell).toContain("useCardStop(isMobile)");
    expect(shell).toContain("expand={expand}");
  });
});

describe("the list marks the open record", () => {
  const draw = (selectedId: string | null) =>
    renderToStaticMarkup(
      createElement(RecordList as unknown as ComponentType<Record<string, unknown>>, {
        entity: "deals",
        rows: [{ id: "a", name: "Acme" }, { id: "b", name: "Bolt" }],
        total: 2,
        statuses: [],
        loading: false,
        sort: null,
        direction: "desc",
        onSort: () => undefined,
        selectedId,
        onOpen: () => undefined,
      }),
    );

  it("marks only that row, in the house token for selected", () => {
    const html = draw("b");
    expect(html.match(/aria-current="true"/g)).toHaveLength(1);
    expect(html.match(/bg-primary\/10/g)).toHaveLength(1);
    expect(html.indexOf('aria-current="true"')).toBeGreaterThan(html.indexOf("Acme"));
  });

  it("marks nothing with no card open", () => {
    expect(draw(null)).not.toContain("aria-current");
  });
});
