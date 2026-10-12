// WS-53 CRM-C2 review: the mode reaches every stage colour, the card has a
// full spoken name, a lane is a group, and the picker shows a stored colour.
//
// The runner has no DOM, so each component is rendered to a string.
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BoardColumn } from "@/components/board/BoardColumn";
import { accentForHue } from "@/lib/statusAccent";

import { stageAccent, type BoardLane } from "../lib/board";
import { cardLabel } from "../lib/cardLabel";
import { colorOptions } from "../lib/colorOptions";
import type { CrmMode } from "../lib/connect";
import { CrmModeContext } from "../lib/modeContext";
import type { Status } from "../lib/types";
import KanbanBoard from "./KanbanBoard";
import StatusPill from "./StatusPill";

const noop = () => undefined;

// Every mirrored row is `gray` (crm_platform.md §20.1 fact 6).
const WON: Status = {
  id: "won",
  name: "Closed Won",
  color: "gray",
  position: 50,
  type: "won",
  is_default: false,
};

const inMode = (mode: CrmMode, node: ReactElement) =>
  renderToStaticMarkup(createElement(CrmModeContext.Provider, { value: mode }, node));

const pill = (mode: CrmMode) =>
  inMode(mode, createElement(StatusPill, { status: WON, statuses: [WON] }));

const board = (mode: CrmMode) =>
  renderToStaticMarkup(
    createElement(KanbanBoard, {
      lanes: [{ status: WON, rows: [], count: 0, amount: 0, weighted: 0 }] as unknown as BoardLane[],
      loading: false,
      mode,
      onMove: noop,
      onOpen: noop,
    }),
  );

describe("the CRM mode reaches every stage colour", () => {
  it("draws a mirrored won stage green on the board and in the status pill", () => {
    const green = accentForHue("green");
    expect(stageAccent(WON, "mirror")).toBe(green);
    expect(board("mirror")).toContain(green.chip);
    expect(pill("mirror")).toContain(green.dot);
    expect(pill("mirror")).not.toContain(accentForHue("gray").dot);
  });

  it("keeps the stored gray in Native mode, on both", () => {
    const gray = accentForHue("gray");
    expect(board("native")).toContain(gray.chip);
    expect(pill("native")).toContain(gray.dot);
  });

  it("reads null outside the provider, which keeps the stored colour", () => {
    const bare = renderToStaticMarkup(
      createElement(StatusPill, { status: WON, statuses: [WON] }),
    );
    expect(bare).toContain(accentForHue("gray").dot);
  });
});

describe("the CRM card's spoken name", () => {
  it("carries the money, the company, the next step and the warnings", () => {
    const label = cardLabel({
      title: "Fixture set",
      amount: "₹2,50,000",
      detail: "Bosch India",
      next: { key: "next", label: "Call Priya today", tone: "accent" },
      needsReason: true,
    });
    expect(label).toBe("Fixture set, ₹2,50,000, Bosch India, Call Priya today, Needs a lost reason");
  });

  it("says Overdue, so the red edge is not the only signal", () => {
    expect(cardLabel({ title: "Lathe", overdue: true })).toBe("Lathe, Overdue");
    // The chip already says it, and the name says it once.
    expect(
      cardLabel({ title: "Lathe", overdue: true, next: { key: "next", label: "Overdue 3d", tone: "danger" } }),
    ).toBe("Lathe, Overdue 3d");
  });

  it("is the name the board gives the card", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, {
        lanes: [
          {
            status: { ...WON, type: "open", name: "Proposal" },
            rows: [
              {
                id: "d1",
                name: "Fixture set",
                amount: 250000,
                currency: "INR",
                organization_name: "Bosch India",
                owner_email: "a@x.test",
                status_changed_at: null,
              },
            ],
            count: 1,
            amount: 250000,
            weighted: 0,
          },
        ] as unknown as BoardLane[],
        loading: false,
        onMove: noop,
        onOpen: noop,
      }),
    );
    expect(html).toMatch(/role="button"[^>]*aria-label="Fixture set, ₹2,50,000, Bosch India/);
  });
});

describe("a lane is a group, not a landmark", () => {
  it("draws role=group with the lane name, and no section", () => {
    const html = renderToStaticMarkup(
      createElement(BoardColumn, {
        header: { accent: accentForHue("blue"), title: "Proposal", count: 1 },
      }),
    );
    expect(html).toMatch(/^<div role="group" aria-label="Proposal"/);
    expect(html).not.toContain("<section");
  });
});

describe("the stage colour picker shows what is stored", () => {
  it("offers the six hues for a stored hue", () => {
    expect(colorOptions("violet").map((o) => o.value)).toEqual([
      "gray",
      "red",
      "amber",
      "green",
      "blue",
      "violet",
    ]);
  });

  it.each([
    ["orange", "amber"],
    ["teal", "green"],
    ["sky", "blue"],
  ])("puts a stored %s first, as custom, with the hue it draws", (stored, hue) => {
    const [first, ...rest] = colorOptions(stored);
    expect(first).toEqual({ value: stored, label: stored, hint: `custom, draws ${hue}` });
    expect(rest).toHaveLength(6);
  });
});
