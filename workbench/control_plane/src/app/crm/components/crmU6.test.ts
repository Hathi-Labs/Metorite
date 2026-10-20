// CRM-U6: the stage order is its own setting, and each stage has a win chance.
// The runner has no DOM, so the grids render to a string.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Status, StatusType } from "../lib/types";
import PipelineSettings from "./PipelineSettings";

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
      onReorderStatuses: async () => undefined,
      onSaveLostReason: async () => true,
      onRemoveLostReason: async () => true,
      onReorderLostReasons: async () => undefined,
    })
  );
}

describe.each([
  ["Native", false],
  ["Mirror", true],
])("%s stage settings", (_name, readOnly) => {
  const html = render(readOnly);

  it("draws the board-order strip with the win chance of each stage", () => {
    const strip = html.slice(html.indexOf('aria-label="Board order"'));
    expect(strip).toContain("Proposal");
    expect(strip).toContain("60%");
    // The order follows the list, not the win chance: 60% comes before 10%.
    expect(strip.indexOf("Proposal")).toBeLessThan(strip.indexOf("Qualify"));
  });

  it("gives each row Up and Down buttons", () => {
    expect(html.match(/aria-label="Move up"/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html.match(/aria-label="Move down"/g)?.length).toBeGreaterThanOrEqual(5);
  });

  it("labels the win chance and a labelled Save", () => {
    expect(html).toContain("Win chance");
    expect(html).toContain(">Save<");
    expect(html).not.toContain('aria-label="Probability"');
  });

  it("disables the win chance of a won and a lost stage, and says why", () => {
    expect(html).toContain("A won stage always forecasts 100%");
    expect(html).toContain("A lost stage always forecasts 0%");
  });
});

describe("Native stage settings", () => {
  const html = render(false);

  it("shows plain type words, never the raw value", () => {
    expect(html).not.toContain(">on_hold<");
    expect(html).toContain("On hold");
  });

  it("folds the rules behind a toggle", () => {
    expect(html).toContain(">Rules<");
    expect(html).not.toContain("Rot threshold in days");
  });

  it("gives lead statuses a win chance input as well", () => {
    expect(render(false, "lead-statuses")).toContain('aria-label="Win chance"');
  });
});

describe("the fences", () => {
  const source = readFileSync(new URL("../lib/board.ts", import.meta.url), "utf8");

  it("keeps the probability out of orderLanes", () => {
    const body = source.slice(source.indexOf("export function orderLanes"), source.indexOf("export function toBoardLanes"));
    expect(body).not.toContain("probability");
  });
});
