// CRM-U6: the stage order is its own setting, and each stage has a win chance.
// The runner has no DOM, so the lists and the Edit dialog render to a string.
// CRM-U11 moved the win chance and the rules into the Edit dialog.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Status, StatusKind, StatusType } from "../lib/types";
import PipelineSettings, { MirrorStageForm, NativeStageForm } from "./PipelineSettings";

const status = (id: string, type: StatusType, position: number, probability: number): Status => ({
  id,
  name: id,
  color: "gray",
  position,
  type,
  is_default: false,
  probability,
});

function render(readOnly: boolean, section: "stages" | "lead-statuses" = "stages"): string {
  return renderToStaticMarkup(
    createElement(PipelineSettings, {
      section,
      showAdminCards: readOnly,
      readOnly,
      dealStatuses: [
        status("Proposal", "ongoing", 10, 60),
        status("Qualify", "open", 20, 10),
        status("Parked", "on_hold", 30, 5),
        status("Closed Won", "won", 40, 100),
        status("Closed Lost", "lost", 50, 0),
      ],
      leadStatuses: [status("New", "open", 10, 5)],
      lostReasons: [],
      saving: false,
      onSaveStatus: async () => true,
      onRemoveStatus: async () => true,
      onSaveLostReason: async () => true,
      onRemoveLostReason: async () => true,
      onReorderLostReasons: async () => undefined,
    })
  );
}

/** The body of the Edit dialog. `Modal` portals, so a test renders the form. */
function renderForm(mirror: boolean, stage: Status, kind: StatusKind = "deal"): string {
  const props = { kind, status: stage, saving: false, onSave: async () => true, onCancel: () => undefined };
  return renderToStaticMarkup(
    mirror
      ? createElement(MirrorStageForm, props)
      : createElement(NativeStageForm, { ...props, onAdd: async () => true, onRemove: async () => true })
  );
}

describe.each([
  ["Native", false],
  ["Mirror", true],
])("%s stage settings", (_name, readOnly) => {
  const html = render(readOnly);

  it("is the board order: the list follows the stage order, not the win chance", () => {
    // CRM-U11 (D130): the board-order strip is gone, and the list IS the
    // board. Proposal (60%) still comes before Qualify (10%).
    expect(html).not.toContain('aria-label="Board order"');
    const rows = [...html.matchAll(/aria-label="Edit ([^"]+)"/g)].map((m) => m[1]);
    expect(rows).toEqual(["Proposal", "Qualify", "Parked", "Closed Won", "Closed Lost"]);
    expect(html).toContain(">60%<");
  });

  it("gives each row Up and Down buttons", () => {
    expect(html.match(/aria-label="Move up"/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html.match(/aria-label="Move down"/g)?.length).toBeGreaterThanOrEqual(5);
  });

  it("labels the win chance and a labelled Save in the Edit dialog, not on the row", () => {
    // CRM-U11: the row shows the win chance as text, and the dialog edits it.
    expect(html).not.toContain(">Save<");
    const form = renderForm(readOnly, status("Qualify", "open", 20, 10));
    expect(form).toContain("Win chance");
    expect(form).toContain(">Save<");
    expect(form).not.toContain('aria-label="Probability"');
  });

  it("disables the win chance of a won and a lost stage in the dialog, and says why", () => {
    const won = renderForm(readOnly, status("Closed Won", "won", 40, 100));
    const lost = renderForm(readOnly, status("Closed Lost", "lost", 50, 0));
    expect(won).toContain("A won stage always forecasts 100%");
    expect(won).toMatch(/value="100"[^>]*disabled=""|disabled=""[^>]*value="100"/);
    expect(lost).toContain("A lost stage always forecasts 0%");
    expect(lost).toMatch(/value="0"[^>]*disabled=""|disabled=""[^>]*value="0"/);
  });
});

describe("Native stage settings", () => {
  const html = render(false);

  it("shows plain type words, never the raw value", () => {
    expect(html).not.toContain(">on_hold<");
    expect(html).toContain("On hold");
  });

  it("keeps the rules of a deal stage in the dialog, with no Rules toggle", () => {
    // CRM-U11: the Rules toggle went. The dialog holds the warning days and
    // the required fields.
    expect(html).not.toContain(">Rules<");
    const form = renderForm(false, status("Qualify", "open", 20, 10));
    expect(form).toContain("Warn when a deal stays more than");
    expect(form).toContain("Fields a deal needs to enter this stage");
  });

  it("gives lead statuses a win chance input as well", () => {
    const form = renderForm(false, status("New", "open", 10, 5), "lead");
    expect(form).toContain("Win chance");
    expect(form).toMatch(/<input[^>]*type="number"/);
  });
});

describe("the fences", () => {
  const source = readFileSync(new URL("../lib/board.ts", import.meta.url), "utf8");

  it("keeps the probability out of orderLanes", () => {
    const body = source.slice(source.indexOf("export function orderLanes"), source.indexOf("export function toBoardLanes"));
    expect(body).not.toContain("probability");
  });
});
